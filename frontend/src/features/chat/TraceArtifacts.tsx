import { Download } from 'lucide-react';
import type { Approval, GeneratedImage, LocalFile, Message } from '../../types';
import { ApprovalCard } from '../google/ApprovalCard';
import { FileEditCard, type FileEditProposal } from '../files/FileEditCard';

function sourceUrlKey(url: string) {
  try {
    return new URL(url).href;
  } catch {
    return url.trim();
  }
}
export function TraceArtifacts({ message }: { message: Message }) {
  const sourceUrls = new Set<string>();
  return (
    <>
      {message.traces?.map((trace, index) => {
        const result = trace.result as
          | {
              approval?: Approval;
              images?: GeneratedImage[];
              job_id?: string;
              sources?: { url: string; title?: string }[];
              file_edit?: FileEditProposal;
            }
          | undefined;
        const sources = [
          ...((trace.web_sources as { url: string; title?: string }[] | undefined) ?? []),
          ...(result?.sources ?? []),
        ].filter((source) => {
          const key = sourceUrlKey(source.url);
          if (sourceUrls.has(key)) return false;
          sourceUrls.add(key);
          return true;
        });
        const file = trace.file as LocalFile | undefined;
        return (
          <div className="trace-artifacts" key={index}>
            {file ? (
              <a className="artifact-link" href={`/api/files/${file.id}/content`} download>
                <Download size={17} />
                <span>{file.filename}</span>
              </a>
            ) : null}
            {sources?.length ? (
              <div className="chat-sources">
                {sources.map((source) => (
                  <a href={source.url} key={source.url} target="_blank" rel="noreferrer">
                    {source.title || source.url}
                  </a>
                ))}
              </div>
            ) : null}
            {result?.images?.map((image) => (
              <a
                key={image.id}
                href={image.content_url || `/api/images/${image.id}/content`}
                target="_blank"
                rel="noreferrer"
              >
                <img
                  className="chat-image"
                  src={image.content_url || `/api/images/${image.id}/content`}
                  alt={image.prompt}
                />
              </a>
            ))}
            {result?.approval ? <ApprovalCard approval={result.approval} /> : null}
            {result?.file_edit ? <FileEditCard proposal={result.file_edit} /> : null}
          </div>
        );
      })}
    </>
  );
}
