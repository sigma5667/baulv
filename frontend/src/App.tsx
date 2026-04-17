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
import { DashboardPage } from "./pages/DashboardPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { PlanAnalysisPage } from "./pages/PlanAnalysisPage";
import { LVEditorPage } from "./pages/LVEditorPage";
import { ProfilePage } from "./pages/ProfilePage";
import { SubscriptionPage } from "./pages/SubscriptionPage";
import { ImpressumPage } from "./pages/ImpressumPage";
import { DatenschutzPage } from "./pages/DatenschutzPage";
import { AGBPage } from "./pages/AGBPage";

function AuthenticatedApp() {
  return (
    <ProtectedRoute>
      <AppShell>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/projects/:id/plans" element={<PlanAnalysisPage />} />
          <Route path="/projects/:id/lv/:lvId?" element={<LVEditorPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/subscription" element={<SubscriptionPage />} />
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
      <Route path="/password-reset" element={<PasswordResetPage />} />

      {/* Legal pages — always publicly reachable, regardless of auth state. */}
      <Route path="/impressum" element={<ImpressumPage />} />
      <Route path="/datenschutz" element={<DatenschutzPage />} />
      <Route path="/agb" element={<AGBPage />} />

      {/* Protected app routes */}
      <Route path="/app/*" element={<AuthenticatedApp />} />

      {/* Fallback */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </RootErrorBoundary>
  );
}
