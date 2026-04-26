export function SettingsIcon({ className = 'settings-launcher__icon' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
      <path
        d="M9.7 3.2h4.6l.5 2.3c.5.2 1 .5 1.4.8l2.2-.7 2.3 4-1.7 1.6v1.6l1.7 1.6-2.3 4-2.2-.7c-.4.3-.9.6-1.4.8l-.5 2.3H9.7l-.5-2.3c-.5-.2-1-.5-1.4-.8l-2.2.7-2.3-4L5 12.8v-1.6L3.3 9.6l2.3-4 2.2.7c.4-.3.9-.6 1.4-.8l.5-2.3Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.55"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="3.1" fill="none" stroke="currentColor" strokeWidth="1.55" />
    </svg>
  );
}
