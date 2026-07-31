// src/layouts/ChatLayout.jsx

import { Outlet } from "react-router-dom";

export default function ChatLayout() {
  return (
    <div className="w-full h-full flex flex-col overflow-hidden theme-shell">
      {/* Narrow chat column – centered on mobile, left‑aligned on desktop */}
      <div className="w-full h-full mx-auto max-w-md lg:mx-0 flex flex-col overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}
