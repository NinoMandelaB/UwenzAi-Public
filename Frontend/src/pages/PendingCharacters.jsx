// src/pages/PendingCharacters.jsx

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle, XCircle, Info, ChevronLeft, Loader2 } from "lucide-react";
import PageCard from "../components/PageCard";
import OverlayCard from "../components/OverlayCard";
import MessagePopup from "../components/MessagePopup";
import PendingCharacterDetailsModal from "../components/PendingCharacterDetailsModal"; // NEW
import { useAuth } from "../context/AuthContext";
import {
  listPendingCharacters,
  approveCharacter,
  rejectCharacter,
} from "../services/characterService";
import {
  getCharacterMessage,
  getCharacterTitle,
} from "../utils/characterMessageMapper";
import africanWomanAvatar from "../assets/images/avatars/african-woman.png";
import placeholderAvatar from "../assets/images/avatars/placeholder.png";

function PendingStatusPill() {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-amber-400/30 bg-slate-900/70 px-3 py-1 text-[10px] font-bold uppercase text-white backdrop-blur-md">
      <span className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
      Review Pending
    </span>
  );
}

function normalizeCharacter(character) {
  return {
    id: character?.character_id || character?.id,
    characterId: character?.character_id,
    name: character?.name || "",
    role: character?.short_description || "",
    mood: character?.mood || "",
    category: character?.category || "",
    intro: character?.intro || "",
    avatarUrl: character?.avatar_url || null,
    nsfwEnabled: character?.nsfw_enabled,
    primaryLanguage: character?.primary_language,
    memoryEnabled: character?.memory_enabled,
    status: character?.status,
  };
}

export default function PendingCharacters() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();

  const [characters, setCharacters] = useState([]);
  const [rawCharacters, setRawCharacters] = useState([]); // NEW – store full API data
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState(false);

  const [loadingImages, setLoadingImages] = useState({});
  const [selectedRawCharacter, setSelectedRawCharacter] = useState(null); // NEW
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  const [rejectingId, setRejectingId] = useState(null);
  const [rejectionReason, setRejectionReason] = useState("");
  const [popup, setPopup] = useState({
    open: false,
    message: "",
    title: "",
    type: "success",
  });

  const handleImageError = (id, e) => {
    setLoadingImages((prev) => ({ ...prev, [id]: false }));
    e.target.src = placeholderAvatar;
  };

  const handleImageLoad = (id) => {
    setLoadingImages((prev) => ({ ...prev, [id]: false }));
  };

  useEffect(() => {
    if (!isAdmin) {
      navigate("/");
      return;
    }
    fetchPending();
  }, [isAdmin]);

  const showToast = (code, type = "success") => {
    setPopup({
      open: true,
      title: getCharacterTitle(code),
      message: getCharacterMessage(code),
      type,
    });
  };

  async function fetchPending() {
    setIsLoading(true);
    try {
      const response = await listPendingCharacters();
      const apiCharacters = response?.data?.characters || [];
      setRawCharacters(apiCharacters); // store full data for modal

      const normalized = apiCharacters.map(normalizeCharacter);
      const initialLoadingState = {};
      normalized.forEach((c) => {
        initialLoadingState[c.id] = true;
      });
      setLoadingImages(initialLoadingState);
      setCharacters(normalized);
    } catch (error) {
      showToast(error, "error");
    } finally {
      setIsLoading(false);
    }
  }

  const handleApprove = async (id) => {
    try {
      setIsProcessing(true);
      await approveCharacter(id);
      showToast("character_approved_successfully", "success");
      setCharacters((prev) => prev.filter((c) => c.id !== id));
      setRawCharacters((prev) => prev.filter((c) => c.character_id !== id));
      if (isDetailsOpen) setIsDetailsOpen(false);
    } catch (error) {
      showToast(error, "error");
    } finally {
      setIsProcessing(false);
    }
  };

  const handleReject = async (id, reason) => {
    if (!reason || !reason.trim()) {
      showToast("Please provide a reason.", "error");
      return;
    }
    try {
      setIsProcessing(true);
      await rejectCharacter(id, { rejection_reason: reason });
      showToast("character_rejected_successfully", "success");
      setCharacters((prev) => prev.filter((c) => c.id !== id));
      setRawCharacters((prev) => prev.filter((c) => c.character_id !== id));
      setRejectingId(null);
      setRejectionReason("");
      if (isDetailsOpen) setIsDetailsOpen(false);
    } catch (error) {
      showToast(error, "error");
    } finally {
      setIsProcessing(false);
    }
  };

  // NEW: open modal with the raw character data
  const handleOpenDetails = (characterId) => {
    const raw = rawCharacters.find((c) => c.character_id === characterId);
    if (raw) {
      setSelectedRawCharacter(raw);
      setIsDetailsOpen(true);
    }
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <MessagePopup
        open={popup.open}
        title={popup.title}
        message={popup.message}
        type={popup.type}
        onClose={() => setPopup({ ...popup, open: false })}
      />

      {/* NEW MODAL */}
      <PendingCharacterDetailsModal
        isOpen={isDetailsOpen}
        character={selectedRawCharacter}
        onClose={() => setIsDetailsOpen(false)}
        onApprove={handleApprove}
        onReject={handleReject}
        isProcessing={isProcessing}
      />

      <header className="flex items-center gap-4 mb-8 px-4 sm:px-0">
        <button
          onClick={() => navigate(-1)}
          className="p-2 rounded-full theme-bg-soft theme-text border theme-border transition hover:opacity-80"
        >
          <ChevronLeft size={20} />
        </button>
        <div>
          <h2 className="text-2xl font-bold theme-text">Review Queue</h2>
          <p className="text-sm theme-text-muted">Awaiting your approval.</p>
        </div>
      </header>

      <section className="space-y-6 px-4 sm:px-0">
        {isLoading ? (
          <div className="py-20 text-center">
            <div className="h-8 w-8 border-4 border-[var(--color-accent)] border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <p className="theme-text-muted">Fetching characters...</p>
          </div>
        ) : characters.length > 0 ? (
          characters.map((character) => {
            const isImgLoading = loadingImages[character.id] ?? true;

            return (
              <div key={character.id} className="relative">
                <OverlayCard
                  image={character.avatarUrl || africanWomanAvatar}
                  onImageError={(e) => handleImageError(character.id, e)}
                  onImageLoad={() => handleImageLoad(character.id)}
                  title={character.name}
                  subtitle={character.role}
                  imageHeight="h-80"
                  imageContent={
                    isImgLoading && (
                      <div className="absolute inset-0 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm z-10">
                        <Loader2
                          className="animate-spin text-amber-400"
                          size={32}
                        />
                      </div>
                    )
                  }
                  topRight={<PendingStatusPill />}
                  footer={
                    <div className="flex flex-col gap-3">
                      {rejectingId === character.id ? (
                        <div className="space-y-2 animate-in fade-in zoom-in duration-200">
                          <textarea
                            className="w-full rounded-xl border theme-border p-3 text-xs theme-bg theme-text outline-none focus:ring-1 focus:ring-rose-500"
                            placeholder="Why are you rejecting this?"
                            value={rejectionReason}
                            onChange={(e) => setRejectionReason(e.target.value)}
                            rows={2}
                          />
                          <div className="flex gap-2">
                            <button
                              onClick={() =>
                                handleReject(character.id, rejectionReason)
                              }
                              disabled={isProcessing}
                              className="flex-1 rounded-xl bg-rose-600 py-3 text-xs font-bold text-white shadow-lg disabled:opacity-50"
                            >
                              Confirm Reject
                            </button>
                            <button
                              onClick={() => setRejectingId(null)}
                              className="flex-1 rounded-xl theme-bg-soft theme-text border theme-border py-3 text-xs font-bold"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex flex-col gap-2">
                          <button
                            type="button"
                            onClick={() =>
                              handleOpenDetails(character.characterId)
                            }
                            className="flex items-center justify-center gap-2 rounded-xl px-3 py-3 text-xs font-bold border theme-border theme-bg-soft theme-text transition hover:opacity-80"
                          >
                            <Info size={16} />
                            Review Full Profile
                          </button>
                          <div className="flex gap-2">
                            <button
                              type="button"
                              onClick={() => handleApprove(character.id)}
                              disabled={isProcessing}
                              className="flex-1 rounded-xl bg-emerald-600 text-white py-3 text-xs font-bold shadow-lg shadow-emerald-900/10 transition hover:bg-emerald-500 disabled:opacity-50"
                            >
                              Approve
                            </button>
                            <button
                              type="button"
                              onClick={() => setRejectingId(character.id)}
                              className="flex-1 rounded-xl bg-rose-500/10 text-rose-500 border border-rose-500/20 py-3 text-xs font-bold transition hover:bg-rose-500/20"
                            >
                              Reject
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  }
                >
                  <p className="text-sm leading-relaxed theme-text-muted italic px-1">
                    "{character.intro || "No introduction provided."}"
                  </p>
                </OverlayCard>
              </div>
            );
          })
        ) : (
          <div className="py-20 text-center">
            <CheckCircle
              size={48}
              className="mx-auto text-emerald-500 mb-4 opacity-20"
            />
            <h3 className="text-xl font-bold theme-text">All Clear!</h3>
            <p className="theme-text-muted mt-2">
              No characters are waiting for review.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
