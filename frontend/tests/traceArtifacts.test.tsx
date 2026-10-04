import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TraceArtifacts } from '../src/features/chat/TraceArtifacts';
import type { Message } from '../src/types';

function message(traces: Message['traces']): Message {
  return {
    id: 'source-message',
    conversation_id: 'source-conversation',
    role: 'assistant',
    content: 'Research answer',
    model: null,
    created_at: '2026-10-04T10:00:00Z',
    status: 'complete',
    attachment_ids: [],
    memory_ids: [],
    traces,
  };
}

describe('Chat trace source links', () => {
  it('deduplicates selected fetch and research overlap while retaining other sources', () => {
    const repository = 'https://github.com/searxng/searxng';
    render(
      <TraceArtifacts
        message={message([
          {
            tool: 'web_fetch',
            result: { sources: [{ url: repository, title: 'Selected repository' }] },
          },
          {
            tool: 'web_research',
            result: {
              sources: [
                { url: repository, title: 'Repeated repository' },
                { url: 'https://docs.searxng.org/', title: 'SearXNG documentation' },
                { url: `${repository}/issues`, title: 'Project issues' },
              ],
            },
          },
        ])}
      />,
    );
    expect(screen.getAllByRole('link')).toHaveLength(3);
    expect(
      screen.getAllByRole('link').filter((link) => link.getAttribute('href') === repository),
    ).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'Selected repository' })).toHaveAttribute(
      'href',
      repository,
    );
    expect(screen.queryByRole('link', { name: 'Repeated repository' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'SearXNG documentation' })).toHaveAttribute(
      'href',
      'https://docs.searxng.org/',
    );
    expect(screen.getByRole('link', { name: 'Project issues' })).toHaveAttribute(
      'href',
      `${repository}/issues`,
    );
  });

  it('normalizes URL syntax without merging different queries, fragments or artifact links', () => {
    const normalized = 'https://example.com/guide?lang=en#install';
    const image = { id: 'image-one', prompt: 'Actual artifact fixture', content_url: normalized };
    const file = { id: 'file-one', filename: 'guide.md' };
    const { container } = render(
      <TraceArtifacts
        message={message([
          {
            web_sources: [
              { url: 'https://EXAMPLE.com:443/guide?lang=en#install', title: 'Selected guide' },
            ],
            file,
            result: { sources: [{ url: normalized, title: 'Duplicate guide' }], images: [image] },
          },
          {
            file,
            result: {
              images: [image],
              sources: [
                { url: 'https://example.com/guide?lang=fr#install', title: 'French guide' },
                { url: 'https://example.com/guide?lang=en#usage', title: 'Usage section' },
                {
                  url: 'https://example.com/guide?lang=en&utm_source=site#install',
                  title: 'Distinct query',
                },
              ],
            },
          },
        ])}
      />,
    );
    expect(container.querySelectorAll('.chat-sources a')).toHaveLength(4);
    expect(screen.queryByRole('link', { name: 'Duplicate guide' })).not.toBeInTheDocument();
    for (const title of ['Selected guide', 'French guide', 'Usage section', 'Distinct query'])
      expect(screen.getByRole('link', { name: title })).toBeVisible();
    expect(screen.getAllByRole('link', { name: 'guide.md' })).toHaveLength(2);
    expect(screen.getAllByRole('img', { name: 'Actual artifact fixture' })).toHaveLength(2);
  });
});
