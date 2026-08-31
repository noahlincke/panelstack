import { useEffect, useState } from 'react';

type CoverImageProps = {
  src?: string;
  alt: string;
  /** Shown when there is no source, or every source fails to load. */
  placeholderLabel: string;
  className?: string;
};

/**
 * Covers come from provider URLs and local archive pages, both of which can 404
 * on a machine that does not hold the file. Failing over to a labelled
 * placeholder keeps a broken source from rendering as an empty tile.
 */
export function CoverImage({ src, alt, placeholderLabel, className = 'poster-tile__image' }: CoverImageProps) {
  const [hasFailed, setHasFailed] = useState(false);

  useEffect(() => {
    setHasFailed(false);
  }, [src]);

  if (src && !hasFailed) {
    return (
      <img
        src={src}
        alt={alt}
        className={className}
        loading="lazy"
        decoding="async"
        onError={() => setHasFailed(true)}
      />
    );
  }

  return (
    <div className="poster-tile__placeholder">
      <span>{placeholderLabel}</span>
    </div>
  );
}
