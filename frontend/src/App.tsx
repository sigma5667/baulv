import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import { AppShell } from "./components/layout/AppShell";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { PWAInstallPrompt } from "./components/PWAInstallPrompt";
import { CookieBanner } from "./components/CookieBanner";
import { ErrorOverlay, RootErrorBoundary } from "./components/ErrorOverlay";
import { LandingPage } from "./pages/LandingPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { PasswordResetPage } from "./pages/PasswordResetPage";
import { PasswortVergessenPage } from "./pages/PasswortVergessenPage";
import { PasswortZuruecksetzenPage } from "./pages/PasswortZuruecksetzenPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { StructurePage } from "./pages/StructurePage";
import { PlanAnalysisPage } from "./pages/PlanAnalysisPage";
import { LVEditorPage } from "./pages/LVEditorPage";
import { TemplatesPage } from "./pages/TemplatesPage";
import { ProfilePage } from "./pages/ProfilePage";
import { ApiKeysPage } from "./pages/ApiKeysPage";
import { SubscriptionPage } from "./pages/SubscriptionPage";
import { ChatPage } from "./pages/ChatPage";
import { ImpressumPage } from "./pages/ImpressumPage";
import { DatenschutzPage } from "./pages/DatenschutzPage";
import { AGBPage } from "./pages/AGBPage";
import { ApiPricingPage } from "./pages/ApiPricingPage";
import { DevelopersPage } from "./pages/DevelopersPage";
import { PrivacySettingsPage } from "./pages/PrivacySettingsPage";
import { AdminAnalyticsPage } from "./pages/AdminAnalyticsPage";

function AuthenticatedApp() {
  return (
    <ProtectedRoute>
      <AppShell>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/projects/:id/structure" element={<StructurePage />} />
          <Route path="/projects/:id/plans" element={<PlanAnalysisPage />} />
          <Route path="/projects/:id/lv/:lvId?" element={<LVEditorPage />} />
          <Route path="/templates" element={<TemplatesPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/api-keys" element={<ApiKeysPage />} />
          <Route path="/subscription" element={<SubscriptionPage />} />
          {/* v23.8 — privacy settings + admin analytics. Both routes
              are mounted unconditionally; the admin page renders a
              local 403 fallback for non-admins (matches the backend
              gate) so we don't need a conditional route. */}
          <Route
            path="/settings/datenschutz"
            element={<PrivacySettingsPage />}
          />
          <Route
            path="/admin/analytics"
            element={<AdminAnalyticsPage />}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell>
    </ProtectedRoute>
  );
}

export default function App() {
  const { user, isLoading } = useAuth();

  return (
    <RootErrorBoundary>
    <ErrorOverlay />
    <PWAInstallPrompt />
    <CookieBanner />
    <Routes>
      {/* Public routes */}
      <Route
        path="/"
        element={
          isLoading ? (
            <div className="flex h-screen items-center justify-center">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            </div>
          ) : user ? (
            <Navigate to="/app" replace />
          ) : (
            <LandingPage />
          )
        }
      />
      <Route path="/login" element={user && !isLoading ? <Navigate to="/app" replace /> : <LoginPage />} />
      <Route path="/register" element={user && !isLoading ? <Navigate to="/app" replace /> : <RegisterPage />} />
      {/* Old English route — kept for backward compatibility with
          any external link or bookmark from before v23.4. New flow
          is at /passwort-vergessen + /passwort-zuruecksetzen. */}
      <Route path="/password-reset" element={<PasswordResetPage />} />
      {/* DS-3 (v23.4) — functional password-reset flow. */}
      <Route path="/passwort-vergessen" element={<PasswortVergessenPage />} />
      <Route
        path="/passwort-zuruecksetzen"
        element={<PasswortZuruecksetzenPage />}
      />

      {/* Legal pages — always publicly reachable, regardless of auth state. */}
      <Route path="/impressum" element={<ImpressumPage />} />
      <Route path="/datenschutz" element={<DatenschutzPage />} />
      <Route path="/agb" element={<AGBPage />} />

      {/* v23.7 — public marketing + technical landing pages for the
          MCP API. Both are reachable without authentication; the
          tier CTAs link into the existing /app/api-keys flow when
          the user is logged in, and the ProtectedRoute bounces them
          to /login otherwise. */}
      <Route path="/api-pricing" element={<ApiPricingPage />} />
      <Route path="/developers" element={<DevelopersPage />} />

      {/* Protected app routes */}
      <Route path="/app/*" element={<AuthenticatedApp />} />

      {/* Fallback */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </RootErrorBoundary>
  );
}
