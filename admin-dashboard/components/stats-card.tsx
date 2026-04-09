interface StatsCardProps {
  label: string;
  value: number | string;
}

export default function StatsCard({ label, value }: StatsCardProps) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-6 shadow-sm">
      <p className="text-sm font-medium text-[var(--text-muted)]">{label}</p>
      <p className="mt-2 text-3xl font-semibold text-[var(--text-primary)]">{value}</p>
    </div>
  );
}
