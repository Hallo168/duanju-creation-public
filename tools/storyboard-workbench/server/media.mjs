import path from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import {
  runProcess,
  providerUrl,
  providerHeaders,
} from './model-providers.mjs';
import { WorkbenchError } from './storage.mjs';

export const PENCIL_PROMPT = '不改变图片，把图片风格转换成素描彩铅画';
export class ImageRequestError extends WorkbenchError {
  constructor(message, outcome = 'unknown_outcome') {
    super(message, 502);
    this.outcome = outcome;
  }
}

export async function limitedBytes(response, limit = 90_000_000) {
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > limit)
      throw new WorkbenchError('接口返回的文件过大，已停止接收。', 413);
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

export async function preflightImage(provider, fetcher = fetch) {
  let response;
  try {
    response = await fetcher(providerUrl(provider, provider.modelsEndpoint), {
      headers: providerHeaders(provider, false),
      redirect: 'error',
      signal: AbortSignal.timeout(20_000),
    });
  } catch {
    throw new WorkbenchError(
      `${provider.label} 暂时无法连接，尚未提交生成。`,
      409,
    );
  }
  if (!response.ok)
    throw new WorkbenchError(
      `${provider.label} 模型目录返回 HTTP ${response.status}，尚未提交生成。`,
      409,
    );
  const body = await response.json();
  const models = (body.data || body.models || []).map(
    (item) => item.id || item.name?.replace(/^models\//, ''),
  );
  if (!models.includes(provider.model))
    throw new WorkbenchError(
      `${provider.label} 当前目录没有模型 ${provider.model}，尚未提交生成。`,
      409,
    );
}

const mime = (file) =>
  /\.jpe?g$/i.test(file)
    ? 'image/jpeg'
    : /\.webp$/i.test(file)
      ? 'image/webp'
      : 'image/png';

// Exactly one create request. Network ambiguity must be reconciled by the user;
// neither the transport nor the job runner retries a paid request.
export async function generateImage(
  provider,
  item,
  { fetcher = fetch, onResponse = async () => {} } = {},
) {
  let body;
  let endpoint = provider.endpoint;
  let headers;
  if (provider.adapter === 'flow_image') {
    if (item.refs.length !== 1)
      throw new WorkbenchError('彩铅派生必须有且只有同状态写实图这一张参考。');
    body = JSON.stringify({
      systemInstruction: {
        parts: [{ text: 'Return exactly one image and no prose.' }],
      },
      contents: [
        {
          role: 'user',
          parts: [
            {
              inlineData: {
                mimeType: mime(item.refs[0]),
                data: (await readFile(item.refs[0])).toString('base64'),
              },
            },
            { text: PENCIL_PROMPT },
          ],
        },
      ],
      generationConfig: {
        responseModalities: ['IMAGE'],
        imageConfig: { aspectRatio: '9:16', imageSize: '1K' },
      },
    });
    headers = providerHeaders(provider);
  } else if (item.refs.length) {
    endpoint = provider.editEndpoint;
    body = new FormData();
    body.set('model', provider.model);
    body.set('prompt', item.prompt);
    body.set('n', '1');
    body.set('size', item.size);
    for (const file of item.refs)
      body.append(
        'image[]',
        new Blob([await readFile(file)], { type: mime(file) }),
        path.basename(file),
      );
    headers = providerHeaders(provider, false);
  } else {
    body = JSON.stringify({
      model: provider.model,
      prompt: item.prompt,
      n: 1,
      size: item.size,
    });
    headers = providerHeaders(provider);
  }
  let response;
  let result;
  try {
    response = await fetcher(providerUrl(provider, endpoint), {
      method: 'POST',
      headers,
      body,
      redirect: 'error',
      signal: AbortSignal.timeout(12 * 60_000),
    });
    const raw = await limitedBytes(response);
    await onResponse(raw);
    result = JSON.parse(raw.toString('utf8'));
  } catch {
    throw new ImageRequestError(
      '生成请求中断或返回内容不完整，结果未知。请核实原请求后再重做。',
    );
  }
  if (!response.ok) {
    const definite =
      response.status >= 400 &&
      response.status < 500 &&
      response.status !== 408;
    throw new ImageRequestError(
      `图片接口返回 HTTP ${response.status}。原始响应已保存在执行记录中。`,
      definite ? 'failed' : 'unknown_outcome',
    );
  }
  const inline = result.candidates
    ?.flatMap((candidate) => candidate.content?.parts || [])
    .find((part) => part.inlineData?.data || part.inline_data?.data);
  const encoded =
    result.data?.[0]?.b64_json ||
    inline?.inlineData?.data ||
    inline?.inline_data?.data;
  if (encoded) return Buffer.from(encoded, 'base64');
  const remoteUrl = result.data?.[0]?.url;
  if (remoteUrl) {
    let url;
    try {
      url = new URL(remoteUrl);
    } catch {
      throw new ImageRequestError(
        '图片地址无效，生成结果已留存，请核对响应。',
        'download_failed',
      );
    }
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password
    )
      throw new ImageRequestError(
        '图片下载地址格式不受支持。',
        'download_failed',
      );
    try {
      const downloaded = await fetcher(url, {
        signal: AbortSignal.timeout(120_000),
      });
      if (!downloaded.ok) throw new Error('download');
      return await limitedBytes(downloaded, 65_000_000);
    } catch {
      throw new ImageRequestError(
        '图片已经返回地址，但下载失败。请从执行记录恢复下载，不要重投生成。',
        'download_failed',
      );
    }
  }
  throw new ImageRequestError(
    '接口没有返回可下载图片，完整响应已保存，请先核实结果。',
  );
}

export function createMediaTools(directory, projectRoot) {
  const script = path.join(directory, 'server', 'image-tools.py');
  const call = async (operation) =>
    JSON.parse(
      await runProcess('python', [script], {
        cwd: projectRoot,
        input: JSON.stringify(operation),
        timeoutMs: 120_000,
      }),
    );
  return {
    verify: (file, expected = null) =>
      call({ action: 'verify', file, expected }),
    normalize: (file, output, expected = null) =>
      call({ action: 'normalize', file, output, expected }),
    thumbnail: (file, output) => call({ action: 'thumbnail', file, output }),
    grid: async (file, output) => {
      const ffmpeg = process.env.STORYBOARD_FFMPEG || 'ffmpeg';
      return call({ action: 'grid', file, output, ffmpeg });
    },
    async save(bytes, rawFile, output, expected) {
      await writeFile(rawFile, bytes, { flag: 'wx' });
      return call({ action: 'normalize', file: rawFile, output, expected });
    },
  };
}
