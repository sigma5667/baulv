import { ReactNode, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  Menu,
  X,
  Building2,
  User,
  CreditCard,
  LogOut,
  MessageSquare,
  LibraryBig,
  KeyRound,
} from "lucide-react";
import { useAuth } from "../../hooks/useAuth";
import { BetaUnlockBanner } from "../BetaUnlockBanner";
import { Footer } from "./Footer";

const navItems = [
  { path: "/app", label: "Dashboard", icon: LayoutDashboard },
  { path: "/app/templates", label: "Vorlagen", icon: LibraryBig },
  { path: "/app/chat", label: "KI-Berater", icon: MessageSquare },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const handleLogout = () => {
    logout();
    navigate("/");
  };

  return (
    <div className="flex h-screen flex-col bg-background">
      {/* Beta-Modus banner — renders only when the server has
          BETA_UNLOCK_ALL_FEATURES=true. Kept OUTSIDE the sidebar/main
          row so it spans the full viewport width instead of just the
          content area. */}
      <BetaUnlockBanner />
      <div className="flex min-h-0 flex-1">
      {/* Sidebar */}
      <aside
        className={`${
          sidebarOpen ? "w-64" : "w-16"
        } flex flex-col border-r bg-card transition-all duration-200`}
      >
        {/* Logo */}
        <div className="flex h-14 items-center justify-between border-b px-4">
          {sidebarOpen && (
            <Link to="/app" className="flex items-center gap-2 font-bold text-primary">
              <Building2 className="h-6 w-6" />
              <span className="text-lg">BauLV</span>
            </Link>
          )}
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="rounded p-1.5 hover:bg-accent"
          >
            {sidebarOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 p-2">
          {navItems.map((item) => {
            const isActive = item.path === "/app"
              ? location.pathname === "/app" || location.pathname === "/app/"
              : location.pathname.startsWith(item.path);
            return (
              <Link
                key={item.path}
                to={item.path}
                className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground"
                }`}
              >
                <item.icon className="h-4 w-4 shrink-0" />
                {sidebarOpen && <span>{item.label}</span>}
              </Link>
            );
          })}
        </nav>

        {/* User section */}
        <div className="border-t p-2 space-y-1">
          <Link
            to="/app/profile"
            className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              location.pathname === "/app/profile"
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            }`}
          >
            <User className="h-4 w-4 shrink-0" />
            {sidebarOpen && <span>Profil</span>}
          </Link>
          <Link
            to="/app/api-keys"
            className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              location.pathname === "/app/api-keys"
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            }`}
          >
            <KeyRound className="h-4 w-4 shrink-0" />
            {sidebarOpen && <span>API-Keys</span>}
          </Link>
          <Link
            to="/app/subscription"
            className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              location.pathname === "/app/subscription"
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            }`}
          >
            <CreditCard className="h-4 w-4 shrink-0" />
            {sidebarOpen && <span>Abonnement</span>}
          </Link>
          <button
            onClick={handleLogout}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
          >
            <LogOut className="h-4 w-4 shrink-0" />
            {sidebarOpen && <span>Abmelden</span>}
          </button>

          {sidebarOpen && user && (
            <div className="px-3 py-2 text-xs text-muted-foreground truncate">
              {user.email}
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex flex-1 flex-col overflow-auto">
        <div className="flex-1">{children}</div>
        {/* Footer with legal links — visible inside the authenticated app too */}
        <Footer />
      </main>
      </div>
    </div>
  );
}
