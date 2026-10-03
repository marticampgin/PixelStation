import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { useLocalStorage } from './useLocalStorage';

export function usePanelState() {
  const [compact, setCompact] = useState(
    () => window.matchMedia?.('(max-width: 950px)').matches ?? false,
  );
  const [desktopLeft, setDesktopLeft] = useLocalStorage('left-open', true);
  const [desktopRight, setDesktopRight] = useLocalStorage('right-open', true);
  const [drawer, setDrawer] = useState<'left' | 'right' | null>(null);

  useEffect(() => {
    const media = window.matchMedia?.('(max-width: 950px)');
    if (!media) return;
    const update = () => setCompact(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (!compact) setDrawer(null);
  }, [compact]);

  const closeCompactPanels = useCallback(() => setDrawer(null), []);
  const setLeftOpen: Dispatch<SetStateAction<boolean>> = (next) => {
    if (!compact) return setDesktopLeft(next);
    setDrawer((previous) => {
      const open = typeof next === 'function' ? next(previous === 'left') : next;
      return open ? 'left' : previous === 'left' ? null : previous;
    });
  };
  const setRightOpen: Dispatch<SetStateAction<boolean>> = (next) => {
    if (!compact) return setDesktopRight(next);
    setDrawer((previous) => {
      const open = typeof next === 'function' ? next(previous === 'right') : next;
      return open ? 'right' : previous === 'right' ? null : previous;
    });
  };
  return {
    compact,
    leftOpen: compact ? drawer === 'left' : desktopLeft,
    rightOpen: compact ? drawer === 'right' : desktopRight,
    setLeftOpen,
    setRightOpen,
    closeCompactPanels,
  };
}
