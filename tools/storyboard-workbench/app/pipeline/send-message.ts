type StudioRequest = <T>(route: string, options?: RequestInit) => Promise<T>;

export async function sendStudioMessage<Job>(
  request: StudioRequest,
  input: { sessionId: string; message: string; model: string },
): Promise<Job> {
  const prepared = await request<{
    token: string;
    confirmationKey: string;
    provider: { model: string };
  }>('/api/studio/preflight', {
    method: 'POST',
    body: JSON.stringify({ sessionId: input.sessionId, message: input.message }),
  });
  if (input.model && prepared.provider.model !== input.model)
    throw new Error('后台返回的模型与本轮选择不一致，请刷新后重试。');

  // Sending authorizes the selected route; consume its one-use token immediately.
  return request<Job>('/api/studio/run', {
    method: 'POST',
    body: JSON.stringify({
      token: prepared.token,
      confirmationKey: prepared.confirmationKey,
      confirmed: true,
      creditConfirmed: true,
      externalWriteConfirmed: true,
    }),
  });
}
