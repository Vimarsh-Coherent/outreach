import { ReactNode } from "react";

/** Vibrant gradient page header banner — used at the top of every app page for
 *  a consistent, product-grade feel. Put primary actions in `actions` styled
 *  for a dark background (e.g. a white solid button + glass secondary buttons). */
export default function PageHero({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="relative overflow-hidden rounded-2xl hero-bg p-7 sm:p-8">
      <div className="blob absolute -right-16 -top-12 h-52 w-52 rounded-full bg-fuchsia-500/30" />
      <div className="blob absolute left-1/3 -bottom-16 h-44 w-44 rounded-full bg-brand-500/30" />
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {eyebrow && <p className="text-xs font-semibold uppercase tracking-widest text-white/60">{eyebrow}</p>}
          <h2 className="mt-1 text-3xl font-bold tracking-tight text-white">{title}</h2>
          {subtitle && <p className="mt-1.5 max-w-2xl text-sm text-white/70">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2.5">{actions}</div>}
      </div>
    </div>
  );
}

/* Convenience button classes for actions placed on the dark hero. */
export const heroBtnPrimary = "btn btn-sm rounded-lg bg-white font-semibold text-slate-900 hover:bg-white/90";
export const heroBtnGhost = "btn btn-sm rounded-lg border border-white/20 bg-white/10 text-white backdrop-blur hover:bg-white/20";
