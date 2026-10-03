import { describe, expect, it } from 'vitest';
import { consumeNdjson } from '../src/api/client';

describe('stream decoder', () => {
  it('preserves tokens split across UTF-8 and NDJSON boundaries', async () => {
    const bytes = new TextEncoder().encode('{"type":"token","content":"Rīga"}\n{"type":"done"}');
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(bytes.slice(0, 29));
        controller.enqueue(bytes.slice(29, 36));
        controller.enqueue(bytes.slice(36));
        controller.close();
      },
    });
    const events: unknown[] = [];
    await consumeNdjson(new Response(body), (event) => events.push(event));
    expect(events).toEqual([{ type: 'token', content: 'Rīga' }, { type: 'done' }]);
  });

  it('rejects invalid events rather than hiding a failed stream', async () => {
    await expect(consumeNdjson(new Response('not json\n'), () => undefined)).rejects.toThrow();
  });
});
