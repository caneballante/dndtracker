import { Map } from "lucide-react";
import Link from "next/link";

export default function NotFound() {
  return (
    <main className="centered-page">
      <Map aria-hidden="true" />
      <p className="eyebrow">Beyond the mapped road</p>
      <h1>This page could not be found.</h1>
      <p>The chronicle may have moved, or the ink may not be dry yet.</p>
      <Link className="button button-primary" href="/">
        Return to the journals
      </Link>
    </main>
  );
}
