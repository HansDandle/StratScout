// Lightweight UI primitives. Tailwind-only, no design-system dependency.

import type { ReactNode, ButtonHTMLAttributes } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
}

export function Card({ children, className = "" }: CardProps) {
  return (
    <div
      className={`bg-(--color-surface) border border-(--color-border) rounded-lg ${className}`}
    >
      {children}
    </div>
  );
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "danger";
  size?: "sm" | "md";
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  children,
  ...rest
}: ButtonProps) {
  const base =
    "inline-flex items-center justify-center font-medium rounded-md transition select-none disabled:opacity-50 disabled:cursor-not-allowed";
  const sizes = {
    sm: "px-3 py-1.5 text-sm",
    md: "px-4 py-2 text-sm",
  };
  const variants = {
    primary:
      "bg-(--color-accent) hover:brightness-110 text-white shadow-[0_0_0_1px_rgba(124,92,255,0.4)]",
    ghost:
      "bg-transparent hover:bg-(--color-surface-2) text-(--color-text) border border-(--color-border)",
    danger:
      "bg-(--color-neg) hover:brightness-110 text-white",
  };
  return (
    <button
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

interface MetricTileProps {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "pos" | "neg" | "warn";
}

export function MetricTile({ label, value, hint, tone = "default" }: MetricTileProps) {
  const valueColor = {
    default: "text-(--color-text)",
    pos: "text-(--color-pos)",
    neg: "text-(--color-neg)",
    warn: "text-(--color-warn)",
  }[tone];

  return (
    <Card className="px-4 py-3 flex-1 min-w-[140px]">
      <div className="text-xs text-(--color-text-dim) uppercase tracking-wider mb-1">
        {label}
      </div>
      <div className={`text-2xl font-semibold tabular ${valueColor}`}>{value}</div>
      {hint && <div className="text-xs text-(--color-text-dim) mt-1">{hint}</div>}
    </Card>
  );
}

interface RiskBadgeProps {
  level: "Low" | "Moderate" | "High";
}

export function RiskBadge({ level }: RiskBadgeProps) {
  const color = {
    Low: "bg-(--color-pos)/15 text-(--color-pos) border-(--color-pos)/40",
    Moderate: "bg-(--color-warn)/15 text-(--color-warn) border-(--color-warn)/40",
    High: "bg-(--color-neg)/15 text-(--color-neg) border-(--color-neg)/40",
  }[level];

  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs rounded-full border ${color}`}>
      Risk: {level}
    </span>
  );
}

export function Spinner() {
  return (
    <div
      className="inline-block w-4 h-4 border-2 border-(--color-text-dim) border-t-transparent rounded-full animate-spin"
      aria-label="Loading"
    />
  );
}
