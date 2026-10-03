import type { EmailMessage } from '../../types';

/** Gmail's SENT/DRAFT labels distinguish our outgoing messages from reply targets. */
export function replyTarget(messages: EmailMessage[]) {
  const inbound = [...messages]
    .reverse()
    .find((message) => !message.label_ids?.some((label) => label === 'SENT' || label === 'DRAFT'));
  const message = inbound ?? messages.at(-1);
  const header = inbound ? inbound.reply_to || inbound.from : (message?.to ?? '');
  return {
    recipient: (header.match(/<([^>]+)>/)?.[1] ?? header).trim(),
    messageId: message?.message_id,
  };
}
