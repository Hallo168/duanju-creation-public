import path from 'node:path';
import { readdir } from 'node:fs/promises';
import { createModelService } from './model-providers.mjs';
import { createAssetService } from './assets.mjs';
import { WorkbenchError } from './storage.mjs';

async function bodyOf(request, limit = 2_000_000) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) throw new WorkbenchError('本次上传超过允许大小。', 413);
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new WorkbenchError('请求数据不是有效 JSON。');
  }
}

export function createWorkbench(options) {
  const models = createModelService(options);
  const assets = createAssetService({ ...options, models });
  const handle = async (request, response, url, send) => {
    if (!url.pathname.startsWith('/api/workbench/')) return false;
    try {
      let result;
      if (
        request.method === 'GET' &&
        url.pathname === '/api/workbench/catalog'
      ) {
        const directories = await readdir(
          path.join(options.projectRoot, 'stories'),
          { withFileTypes: true },
        );
        result = {
          episodes: directories
            .filter((item) => item.isDirectory() && /^\d+[-－]/.test(item.name))
            .map((item) => item.name)
            .sort((a, b) => b.localeCompare(a, 'zh-CN', { numeric: true })),
        };
      } else if (
        request.method === 'GET' &&
        url.pathname === '/api/workbench/settings'
      )
        result = await models.list();
      else if (
        request.method === 'POST' &&
        url.pathname === '/api/workbench/settings'
      )
        result = await models.save(await bodyOf(request));
      else if (
        request.method === 'GET' &&
        url.pathname === '/api/workbench/models'
      )
        result = await models.models(url.searchParams.get('providerId'));
      else if (request.method === 'POST' && url.pathname === '/api/workbench/models')
        result = await models.previewModels((await bodyOf(request)).provider);
      else if (
        request.method === 'GET' &&
        url.pathname === '/api/workbench/assets'
      )
        result = await assets.state(url.searchParams.get('episode'));
      else if (
        request.method === 'GET' &&
        url.pathname === '/api/workbench/assets/media'
      ) {
        await assets.serveImage(response, url);
        return true;
      } else if (request.method === 'POST') {
        const operations = {
          '/api/workbench/assets/prompt': assets.savePrompt,
          '/api/workbench/assets/plan': assets.startPlan,
          '/api/workbench/assets/generate': assets.startImages,
          '/api/workbench/assets/import': assets.importPhoto,
          '/api/workbench/assets/recover': assets.recover,
          '/api/workbench/assets/check': assets.verifyExisting,
        };
        const operation = operations[url.pathname];
        if (!operation) throw new WorkbenchError('没有这个操作。', 404);
        result = await operation(
          await bodyOf(
            request,
            url.pathname.endsWith('/import') ? 29_000_000 : 2_000_000,
          ),
        );
      } else throw new WorkbenchError('接口不存在。', 404);
      send(request, response, 200, result);
    } catch (error) {
      const status = error.status || (error.code === 'ENOENT' ? 404 : 500);
      send(request, response, status, {
        error:
          status === 404
            ? '所选故事或文件不存在。'
            : error instanceof WorkbenchError
              ? error.message
              : '本地操作未完成，请检查运行环境后重试。',
      });
    }
    return true;
  };
  return { models, assets, handle };
}
