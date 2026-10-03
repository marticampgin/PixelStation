import { ChevronDown } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import { useLocalStorage } from '../hooks/useLocalStorage';

export function Collapsible({
  id,
  open,
  children,
  className = '',
}: {
  id: string;
  open: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      id={id}
      className={`collapsible ${open ? 'is-open' : ''} ${className}`}
      aria-hidden={!open}
      inert={!open}
    >
      <div className="collapsible-content">{children}</div>
    </div>
  );
}

export function DisclosureToggle({
  open,
  onClick,
  controls,
  label,
}: {
  open: boolean;
  onClick: () => void;
  controls: string;
  label: string;
}) {
  return (
    <button
      className={`icon-button disclosure-toggle ${open ? 'is-open' : ''}`}
      aria-expanded={open}
      aria-controls={controls}
      aria-label={`${open ? 'Collapse' : 'Expand'} ${label}`}
      onClick={onClick}
    >
      <ChevronDown size={16} strokeWidth={1.8} />
    </button>
  );
}

export function Disclosure({
  title,
  preference,
  children,
  initiallyOpen = true,
}: {
  title: string;
  preference: string;
  children: ReactNode;
  initiallyOpen?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useLocalStorage(preference, initiallyOpen);
  return (
    <section className="disclosure-section">
      <button
        className={`disclosure-heading ${open ? 'is-open' : ''}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        <span>{title}</span>
        <ChevronDown size={16} strokeWidth={1.8} />
      </button>
      <Collapsible id={id} open={open}>
        {children}
      </Collapsible>
    </section>
  );
}
