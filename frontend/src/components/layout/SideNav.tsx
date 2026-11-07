import { NavLink } from "react-router-dom";

const ITEMS = [{ to: "/dashboard", label: "Dashboard" }];

export function SideNav() {
  return (
    <nav
      aria-label="Primary"
      className="hidden w-52 shrink-0 border-r border-border-subtle bg-panel py-3 md:block"
    >
      <ul>
        {ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              className={({ isActive }) =>
                `block border-l-2 px-4 py-2 text-sm ${
                  isActive
                    ? "border-accent bg-raised text-fg"
                    : "border-transparent text-fg-muted hover:bg-raised hover:text-fg"
                }`
              }
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
