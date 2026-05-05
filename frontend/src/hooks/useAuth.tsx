import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import type {
  User,
  FeatureMatrix,
  IndustrySegment,
} from "../types/user";
import {
  fetchMe,
  fetchFeatures,
  loginUser,
  registerUser,
  refreshConsent as apiRefreshConsent,
} from "../api/auth";

const TOKEN_KEY = "baulv_token";

interface AuthContextType {
  user: User | null;
  features: FeatureMatrix | null;
  token: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (data: {
    email: string;
    password: string;
    full_name: string;
    company_name?: string;
    accepted_privacy_version: string;
    accepted_terms_version: string;
    marketing_optin: boolean;
    /** v23.8 — analytics opt-in (default false). */
    analytics_consent?: boolean;
    /** v23.8 — user-self-selected branch. */
    industry_segment?: IndustrySegment | null;
  }) => Promise<void>;
  /** Re-record consent after the user accepts updated legal docs.
   * Updates the user state in-place on success so the
   * ConsentRefreshModal closes itself via the
   * ``needsConsentRefresh(user)`` predicate dropping to false. */
  refreshConsent: (data: {
    accepted_privacy_version: string;
    accepted_terms_version: string;
    marketing_optin: boolean;
    analytics_consent: boolean;
    industry_segment?: IndustrySegment | null;
  }) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
  hasFeature: (feature: keyof FeatureMatrix) => boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [features, setFeatures] = useState<FeatureMatrix | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [isLoading, setIsLoading] = useState(true);

  const setAuth = useCallback((t: string, u: User) => {
    localStorage.setItem(TOKEN_KEY, t);
    setToken(t);
    setUser(u);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
    setFeatures(null);
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const [u, f] = await Promise.all([fetchMe(), fetchFeatures()]);
      setUser(u);
      setFeatures(f);
    } catch {
      logout();
    }
  }, [logout]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await loginUser({ email, password });
      setAuth(res.access_token, res.user);
    },
    [setAuth]
  );

  const register = useCallback(
    async (data: {
      email: string;
      password: string;
      full_name: string;
      company_name?: string;
      accepted_privacy_version: string;
      accepted_terms_version: string;
      marketing_optin: boolean;
    }) => {
      const res = await registerUser(data);
      setAuth(res.access_token, res.user);
    },
    [setAuth]
  );

  const refreshConsent = useCallback(
    async (data: {
      accepted_privacy_version: string;
      accepted_terms_version: string;
      marketing_optin: boolean;
    }) => {
      const updated = await apiRefreshConsent(data);
      setUser(updated);
    },
    []
  );

  const hasFeature = useCallback(
    (feature: keyof FeatureMatrix) => {
      if (!features) return false;
      return !!features[feature];
    },
    [features]
  );

  useEffect(() => {
    if (token) {
      refreshUser().finally(() => setIsLoading(false));
    } else {
      setIsLoading(false);
    }
  }, [token, refreshUser]);

  return (
    <AuthContext.Provider
      value={{
        user,
        features,
        token,
        isLoading,
        login,
        register,
        refreshConsent,
        logout,
        refreshUser,
        hasFeature,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
