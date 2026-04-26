type PosterSizeControlProps = {
  value: number;
  onChange: (value: number) => void;
};

export function PosterSizeControl({ value, onChange }: PosterSizeControlProps) {
  return (
    <label className="poster-size-control">
      <span>Size</span>
      <input
        type="range"
        min="120"
        max="260"
        step="10"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
