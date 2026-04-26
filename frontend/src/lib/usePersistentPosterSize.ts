import { useEffect, useState } from 'react';

function readInitialValue(storageKey: string, fallback: number): number {
  if (typeof window === 'undefined') {
    return fallback;
  }
  const raw = window.localStorage.getItem(storageKey);
  const parsed = raw ? Number(raw) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function usePersistentPosterSize(storageKey: string, fallback: number): [number, (value: number) => void] {
  const [value, setValue] = useState(() => readInitialValue(storageKey, fallback));

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.localStorage.setItem(storageKey, String(value));
  }, [storageKey, value]);

  return [value, setValue];
}
