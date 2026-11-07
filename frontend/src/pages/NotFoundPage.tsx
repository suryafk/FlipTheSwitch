import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-xl py-16 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-fg-muted">This route does not exist.</p>
      <Link to="/dashboard" className="mt-4 inline-block text-accent hover:underline">
        Go to the dashboard
      </Link>
    </div>
  );
}
