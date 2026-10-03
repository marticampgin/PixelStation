import { describe, expect, it } from 'vitest';
import { replyTarget } from '../src/features/gmail/replyTarget';
import type { EmailMessage } from '../src/types';

const message = (extra: Partial<EmailMessage>): EmailMessage => ({
  id: 'one',
  subject: 'Project',
  from: 'client@example.com',
  to: 'owner@example.com',
  body: 'Hello',
  date: '2026-10-03',
  attachments: [],
  label_ids: ['INBOX'],
  message_id: '<inbound@example.com>',
  ...extra,
});
describe('Gmail reply recipient', () => {
  it('replies to the inbound Reply-To when the latest message is our own sent reply', () => {
    expect(
      replyTarget([
        message({ reply_to: 'Client desk <reply@example.com>' }),
        message({
          id: 'two',
          from: 'owner@example.com',
          to: 'client@example.com',
          label_ids: ['SENT'],
          message_id: '<outbound@example.com>',
        }),
      ]),
    ).toEqual({ recipient: 'reply@example.com', messageId: '<inbound@example.com>' });
  });
  it('uses the original recipient for a sent-only thread', () => {
    expect(
      replyTarget([
        message({
          from: 'owner@example.com',
          to: 'Client <client@example.com>',
          label_ids: ['SENT'],
          message_id: '<sent@example.com>',
        }),
      ]),
    ).toEqual({ recipient: 'client@example.com', messageId: '<sent@example.com>' });
  });
  it('does not choose a draft as an inbound reply target', () => {
    expect(
      replyTarget([
        message({}),
        message({ from: 'owner@example.com', to: 'other@example.com', label_ids: ['DRAFT'] }),
      ]).recipient,
    ).toBe('client@example.com');
  });
});
