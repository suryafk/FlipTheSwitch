import type { ReactNode } from "react";

export function Panel({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-md border border-border-subtle bg-panel" aria-label={title}>
      <header className="flex h-10 items-center justify-between border-b border-border-subtle px-4">
        <h2 className="text-xs font-semibold tracking-wider text-fg-muted uppercase">{title}</h2>
        {actions}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
