import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function PublicOnlyRoute() {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
        <p className="text-sm theme-text-muted">Loading...</p>
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/characters" replace />;
  }

  return <Outlet />;
}
