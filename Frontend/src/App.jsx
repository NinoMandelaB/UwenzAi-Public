// src/App.jsx
import { BrowserRouter, Route, Routes, Navigate } from "react-router-dom";
import AuthLayout from "./layouts/AuthLayout";
import MainLayout from "./layouts/MainLayout";

// Restored old chat page imports for testing
import Chat from "./pages/Chat";
import ConversationChat from "./pages/ConversationChat";

import Dashboard from "./pages/Dashboard";
import Characters from "./pages/Characters";
import MyCharacters from "./pages/MyCharacters";
import PendingCharacters from "./pages/PendingCharacters";
import Chats from "./pages/Chats";
import SparkPool from "./pages/SparkPool";
import Transactions from "./pages/Transactions";
import Settings from "./pages/Settings";
import Login from "./pages/Login";
import Register from "./pages/Register";
import VerifyEmail from "./pages/VerifyEmail";
import PasswordRecovery from "./pages/PasswordRecovery";
import ChangePassword from "./pages/ChangePassword";
import AdminCreateCharacter from "./pages/AdminCreateCharacter";
import CreateCharacter from "./pages/CreateCharacter";
import CharacterEdit from "./pages/edit/";
import CharacterDetail from "./pages/CharacterDetail";
import EditProfile from "./pages/EditProfile";
import Profile from "./pages/Profile";
import Contact from "./pages/Contact";
import ManageFlaggedMessages from "./pages/admin/ManageFlaggedMessages";
import ManageFlaggedCharacters from "./pages/admin/ManageFlaggedCharacters";
import ManageOccupations from "./pages/admin/ManageOccupations";
import ManageHobbies from "./pages/admin/ManageHobbies";
import ManagePersonalities from "./pages/admin/ManagePersonalities";
import AdminFeedback from "./pages/admin/Feedback";
import CharacterImageSetup from "./pages/CharacterImageSetup";
import TermsOfService from "./pages/terms/TermsOfService";
import PrivacyPolicy from "./pages/terms/PrivacyPolicy";
import ResponsibleUseGuide from "./pages/terms/ResponsibleUseGuide";
import SubprocessorDisclosure from "./pages/terms/SubprocessorDisclosure";
import PaymentVerify from "./pages/PaymentVerify";

// New Support pages
import Support from "./pages/Support";
import SupportDetail from "./pages/SupportDetail";

import { AuthProvider } from "./context/AuthContext";
import { ChatModalProvider } from "./context/ChatModalContext";
import { WebSocketProvider } from "./context/WebSocketContext";
import { SparkPoolBalanceProvider } from "./context/SparkPoolBalanceContext"; // <-- NEW
import { UnreadProvider } from "./context/UnreadContext";
import ChatModal from "./components/ChatModal";
import AgeGate from "./components/AgeGate";
import ProtectedRoute from "./components/ProtectedRoute";
import PublicOnlyRoute from "./components/PublicOnlyRoute";

export default function App() {
  return (
    <AuthProvider>
      <WebSocketProvider>
        <SparkPoolBalanceProvider>
          {" "}
          {/* <-- NEW: wraps all consumers */}
          <UnreadProvider>
            <ChatModalProvider>
              <BrowserRouter>
                {/* 18+ confirmation for guests, once per browser session.
                    Wraps the entire router so no page (including marketing
                    and legal pages) renders before the gate is answered.
                    Authenticated users are skipped — age is confirmed at
                    sign-up. */}
                <AgeGate>
                  <Routes>
                    {/* Public only routes (guests only) */}
                    <Route element={<PublicOnlyRoute />}>
                      <Route element={<AuthLayout />}>
                        <Route path="/login" element={<Login />} />
                        <Route path="/register" element={<Register />} />
                        <Route path="/verify-email" element={<VerifyEmail />} />
                        <Route
                          path="/password-recovery"
                          element={<PasswordRecovery />}
                        />
                      </Route>
                    </Route>

                    {/* Legal pages – accessible to everyone (guests and authenticated users) */}
                    <Route element={<AuthLayout />}>
                      <Route path="/terms" element={<TermsOfService />} />
                      <Route path="/privacy" element={<PrivacyPolicy />} />
                      <Route
                        path="/responsible-use"
                        element={<ResponsibleUseGuide />}
                      />
                      <Route
                        path="/subprocessors"
                        element={<SubprocessorDisclosure />}
                      />
                    </Route>

                    {/* Main app layout */}
                    <Route element={<MainLayout />}>
                      <Route
                        path="/"
                        element={<Navigate to="/characters" replace />}
                      />
                      <Route path="/characters" element={<Characters />} />
                      <Route path="/dashboard" element={<Dashboard />} />
                      <Route path="/spark_pool" element={<SparkPool />} />
                      {/* Paystack redirect target. Public because guests can buy
                      sparks; ownership of the transaction is proven by the guest
                      credential cookie, not by anything in the URL. */}
                      <Route
                        path="/payment/verify"
                        element={<PaymentVerify />}
                      />
                      <Route path="/transactions" element={<Transactions />} />
                      <Route path="/settings" element={<Settings />} />
                      <Route path="/chats" element={<Chats />} />
                      <Route path="/edit-profile" element={<EditProfile />} />
                      <Route path="/profile" element={<Profile />} />
                      <Route path="/contact" element={<Contact />} />

                      {/* TEMPORARY old chat routes for testing */}
                      <Route path="/chat/:characterId" element={<Chat />} />
                      <Route
                        path="/conversations/:conversationId"
                        element={<ConversationChat />}
                      />

                      {/* Public character detail – no auth required */}
                      <Route
                        path="/characters/:characterId"
                        element={<CharacterDetail />}
                      />

                      {/* Protected routes inside MainLayout */}
                      <Route element={<ProtectedRoute />}>
                        <Route
                          path="/characters/pending"
                          element={<PendingCharacters />}
                        />
                        <Route
                          path="/change-password"
                          element={<ChangePassword />}
                        />
                        <Route
                          path="/characters/create"
                          element={<CreateCharacter />}
                        />
                        <Route
                          path="/characters/:characterId/edit"
                          element={<CharacterEdit />}
                        />
                        <Route
                          path="/characters/:characterId/setup"
                          element={<CharacterImageSetup />}
                        />
                        <Route
                          path="/admin/occupations"
                          element={<ManageOccupations />}
                        />
                        <Route
                          path="/admin/hobbies"
                          element={<ManageHobbies />}
                        />
                        <Route
                          path="/admin/personalities"
                          element={<ManagePersonalities />}
                        />
                        <Route
                          path="/admin/feedback"
                          element={<AdminFeedback />}
                        />
                        <Route
                          path="/admin/flagged-characters"
                          element={<ManageFlaggedCharacters />}
                        />
                        <Route
                          path="/admin/flagged-messages"
                          element={<ManageFlaggedMessages />}
                        />
                        {/* New route for user's own characters */}
                        <Route
                          path="/my-characters"
                          element={<MyCharacters />}
                        />

                        {/* New Support routes */}
                        <Route path="/support" element={<Support />} />
                        <Route
                          path="/support/:ticketId"
                          element={<SupportDetail />}
                        />
                      </Route>
                    </Route>

                    {/* Other protected routes */}
                    <Route element={<ProtectedRoute />}>
                      <Route
                        path="/admin/characters/new"
                        element={<AdminCreateCharacter />}
                      />
                    </Route>
                  </Routes>
                </AgeGate>
              </BrowserRouter>

              {/* ChatModal – still present but not used during this test */}
              <ChatModal />
            </ChatModalProvider>
          </UnreadProvider>
        </SparkPoolBalanceProvider>
      </WebSocketProvider>
    </AuthProvider>
  );
}
