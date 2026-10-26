import type { ReactNode } from 'react'
import { twMerge } from 'tailwind-merge'

/** Joins class names and resolves Tailwind conflicts, the last class wins. */
export function cn(...classes: (string | false | null | undefined)[]): string {
  return twMerge(classes.filter(Boolean).join(' '))
}

export function buttonClass(active = false): string {
  return cn(
    'inline-flex items-center justify-center gap-1 rounded-md border px-2.5 py-1 text-sm disabled:opacity-50',
    active
      ? 'border-sky-600 bg-sky-600 text-white hover:bg-sky-700'
      : 'border-slate-300 bg-white text-slate-800 hover:bg-slate-100',
  )
}

type BadgeTone = 'good' | 'bad' | 'warning' | 'info' | 'neutral'

const BADGE_TONES: Record<BadgeTone, string> = {
  good: 'bg-emerald-100 text-emerald-900',
  bad: 'bg-red-100 text-red-900',
  warning: 'bg-amber-100 text-amber-900',
  info: 'bg-sky-100 text-sky-900',
  neutral: 'bg-slate-200 text-slate-800',
}

export function Badge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium',
        BADGE_TONES[tone],
      )}
    >
      {children}
    </span>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{title}</h2>
      {children}
    </section>
  )
}
