import { cn } from "@/lib/cn";

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        "rounded-card border border-lumi-line bg-lumi-surface shadow-sm shadow-black/[0.03]",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <Card className="p-4">
      <div className="text-sm text-lumi-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{value}</div>
      {hint ? <div className="mt-1 text-xs text-lumi-muted">{hint}</div> : null}
    </Card>
  );
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "good" | "warn" | "bad" | "accent";
  children: React.ReactNode;
}) {
  const tones: Record<string, string> = {
    neutral: "bg-lumi-bg text-lumi-muted border-lumi-line",
    good: "bg-lumi-good/10 text-lumi-good border-lumi-good/30",
    warn: "bg-lumi-warn/10 text-lumi-warn border-lumi-warn/30",
    bad: "bg-lumi-bad/10 text-lumi-bad border-lumi-bad/30",
    accent: "bg-lumi-accent-soft text-lumi-accent border-lumi-accent/30",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs whitespace-nowrap",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Button({
  variant = "primary",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
}) {
  const variants: Record<string, string> = {
    primary: "bg-lumi-accent text-white hover:opacity-90 disabled:opacity-50",
    secondary: "border border-lumi-line bg-lumi-surface text-lumi-ink hover:bg-lumi-accent-soft/50 disabled:opacity-50",
    ghost: "text-lumi-muted hover:bg-lumi-accent-soft/50 hover:text-lumi-ink",
    danger: "bg-lumi-bad text-white hover:opacity-90",
  };
  const sizes: Record<string, string> = {
    sm: "px-2.5 py-1.5 text-xs",
    md: "px-4 py-2 text-sm",
    lg: "px-5 py-2.5 text-base",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-150 disabled:cursor-not-allowed",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded-lg border border-lumi-line bg-lumi-surface px-3 py-2 text-sm text-lumi-ink placeholder:text-lumi-muted/60 transition-colors focus:border-lumi-accent",
        className,
      )}
      {...props}
    />
  );
}

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "w-full rounded-lg border border-lumi-line bg-lumi-surface px-3 py-2 text-sm text-lumi-ink transition-colors focus:border-lumi-accent",
        className,
      )}
      {...props}
    />
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-lumi-muted border-t-transparent",
        className,
      )}
      role="status"
      aria-label="加载中"
    />
  );
}

export function EmptyState({ title, hint, children }: { title: string; hint?: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-16 text-center">
      <div className="text-lg font-medium">{title}</div>
      {hint ? <div className="max-w-sm text-sm text-lumi-muted">{hint}</div> : null}
      {children}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-lumi-bad/30 bg-lumi-bad/10 px-3 py-2 text-sm text-lumi-bad">
      {message}
    </div>
  );
}
