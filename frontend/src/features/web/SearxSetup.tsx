import { Copy } from 'lucide-react';
import { useState } from 'react';
import { errorMessage } from '../../api/client';

const command =
  "$searchSecret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')\n[IO.File]::WriteAllText((Join-Path (Get-Location) 'config/.env'), \"SEARXNG_SECRET=$searchSecret`n\")\ndocker compose --env-file config/.env -f config/docker-compose.optional.yml up -d searxng";
export function SearxSetup() {
  const [notice, setNotice] = useState('');
  return (
    <>
      <p className="subtle">
        Install and start Docker Desktop with Linux containers. On first setup, run these PowerShell
        commands from the project directory to create the private search secret and start SearXNG.
      </p>
      <code className="code-block">{command}</code>
      <div className="row-actions">
        <button
          className="text-button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(command);
              setNotice('Setup commands copied');
            } catch (err) {
              setNotice(errorMessage(err));
            }
          }}
        >
          <Copy size={13} />
          Copy setup commands
        </button>
        <span className="subtle" role="status">
          {notice}
        </span>
      </div>
    </>
  );
}
