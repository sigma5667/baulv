import { Link } from "react-router-dom";
import { Building2 } from "lucide-react";

/**
 * Shared footer with legal links. Rendered on the public landing page and
 * inside the authenticated AppShell so Impressum / Datenschutz / AGB are
 * always one click away (required for Austrian Impressumpflicht).
 */
export function Footer() {
  return (
    <footer className="border-t bg-muted/30 py-6 text-sm text-muted-foreground">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-6 md:flex-row">
        <div className="flex items-center gap-2">
          <Building2 className="h-5 w-5 text-primary" />
          <span className="font-semibold text-foreground">BauLV</span>
          <span className="text-muted-foreground">— &copy; 2026</span>
        </div>
        <nav className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
          <Link to="/impressum" className="hover:text-foreground">
            Impressum
          </Link>
          <Link to="/datenschutz" className="hover:text-foreground">
            Datenschutz
          </Link>
          <Link to="/agb" className="hover:text-foreground">
            AGB
          </Link>
          <a
            href="https://www.austrian-standards.at"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground"
            title="Austrian Standards — externer Link, keine Partnerschaft"
          >
            Powered by Austrian Standards Berechnungsregeln
          </a>
        </nav>
      </div>
    </footer>
  );
}
