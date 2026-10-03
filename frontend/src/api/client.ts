export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData))
    headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, { ...init, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail ?? payload?.error ?? response.statusText;
    throw new ApiError(
      response.status,
      typeof detail === 'string' ? detail : (detail?.message ?? JSON.stringify(detail)),
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const post = <T>(path: string, body?: unknown, signal?: AbortSignal) =>
  request<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
export const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body) });
export const remove = <T = void>(path: string) => request<T>(path, { method: 'DELETE' });

export interface StreamEvent {
  type: string;
  content?: string;
  message?: unknown;
  error?: string;
  status?: string;
  [key: string]: unknown;
}

/** Decode NDJSON across arbitrary network chunk boundaries, including UTF-8. */
export async function consumeNdjson(
  response: Response,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(response.status, payload?.detail ?? response.statusText);
  }
  if (!response.body) throw new Error('The server returned an empty response stream.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  const parse = (line: string) => {
    if (line.trim()) onEvent(JSON.parse(line) as StreamEvent);
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      const lines = pending.split('\n');
      pending = lines.pop() ?? '';
      lines.forEach(parse);
      if (done) {
        parse(pending);
        break;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function streamChat(
  path: string,
  body: unknown,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
) {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  await consumeNdjson(response, onEvent);
}

export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : 'An unexpected error occurred.';
