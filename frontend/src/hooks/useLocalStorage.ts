import { useState, type Dispatch, type SetStateAction } from 'react';

export function useLocalStorage<T>(name: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const key = `pixel-station:v1:${name}`;
  const [value, setValue] = useState<T>(() => {
    try {
      return JSON.parse(localStorage.getItem(key) ?? 'null') ?? initial;
    } catch {
      return initial;
    }
  });
  const update: Dispatch<SetStateAction<T>> = (next) => {
    setValue((previous) => {
      const resolved = typeof next === 'function' ? (next as (value: T) => T)(previous) : next;
      try {
        localStorage.setItem(key, JSON.stringify(resolved));
      } catch {
        /* Layout preferences are nonessential. */
      }
      return resolved;
    });
  };
  return [value, update];
}
