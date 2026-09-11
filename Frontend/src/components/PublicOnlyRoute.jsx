import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function PublicOnlyRoute() {
  const { isAuthenticated, isLoading } = useAuth();
  const { state } = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
        <p className="text-sm theme-text-muted">Loading...</p>
      </div>
    );
  }

  if (isAuthenticated) {
    // Only a fixed, local destination is accepted; never an arbitrary return URL.
    return (
      <Navigate
        to={
          state?.connectGoogle === true
            ? "/settings#google-connection"
            : "/characters"
        }
        replace
      />
    );
  }

  return <Outlet />;
}
