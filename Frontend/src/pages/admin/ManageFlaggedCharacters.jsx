// src/pages/admin/ManageFlaggedCharacters.jsx

import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { useLanguage } from "../../context/LanguageContext";
import {
  listFlaggedCharacters,
  deleteCharacter,
  clearCharacterFlags, // new import
} from "../../services/characterService";
import { getCharacterMessage } from "../../utils/characterMessageMapper";
import ConfirmDialog from "../../components/ConfirmDialog";
import { Loader2, Flag, Trash2, Eye, ShieldOff } from "lucide-react"; // added ShieldOff

export default function ManageFlaggedCharacters() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { user, isAdmin } = useAuth();

  const [characters, setCharacters] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  // Delete character state
  const [characterToDelete, setCharacterToDelete] = useState(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isDeleteConfirmOpen, setIsDeleteConfirmOpen] = useState(false);

  // Clear flags state
  const [characterToClearFlags, setCharacterToClearFlags] = useState(null);
  const [isClearingFlags, setIsClearingFlags] = useState(false);
  const [isClearFlagsConfirmOpen, setIsClearFlagsConfirmOpen] = useState(false);

  // Redirect if not admin
  useEffect(() => {
    if (!user) {
      navigate("/login");
    } else if (!isAdmin) {
      navigate("/characters");
    }
  }, [user, isAdmin, navigate]);

  // Fetch flagged characters
  useEffect(() => {
    async function fetchFlagged() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await listFlaggedCharacters();
        setCharacters(response?.data?.characters || []);
      } catch (err) {
        setError(
          getCharacterMessage(err, "Failed to load flagged characters."),
        );
      } finally {
        setIsLoading(false);
      }
    }
    fetchFlagged();
  }, []);

  // ----- Delete Character flow -----
  const handleDeleteClick = (character) => {
    setCharacterToDelete(character);
    setIsDeleteConfirmOpen(true);
  };

  const handleCancelDelete = () => {
    setIsDeleteConfirmOpen(false);
    setCharacterToDelete(null);
  };

  const handleConfirmDelete = async () => {
    if (!characterToDelete) return;
    setIsDeleting(true);
    try {
      await deleteCharacter(characterToDelete.character_id);
      setCharacters((prev) =>
        prev.filter((c) => c.character_id !== characterToDelete.character_id),
      );
    } catch (err) {
      setError(getCharacterMessage(err, "Failed to delete character."));
    } finally {
      setIsDeleting(false);
      setIsDeleteConfirmOpen(false);
      setCharacterToDelete(null);
    }
  };

  // ----- Clear Flags flow -----
  const handleClearFlagsClick = (character) => {
    setCharacterToClearFlags(character);
    setIsClearFlagsConfirmOpen(true);
  };

  const handleCancelClearFlags = () => {
    setIsClearFlagsConfirmOpen(false);
    setCharacterToClearFlags(null);
  };

  const handleConfirmClearFlags = async () => {
    if (!characterToClearFlags) return;
    setIsClearingFlags(true);
    try {
      await clearCharacterFlags(characterToClearFlags.character_id);
      // Refresh the list – character may disappear if flag_count becomes 0
      const response = await listFlaggedCharacters();
      setCharacters(response?.data?.characters || []);
    } catch (err) {
      setError(getCharacterMessage(err, "Failed to clear flags."));
    } finally {
      setIsClearingFlags(false);
      setIsClearFlagsConfirmOpen(false);
      setCharacterToClearFlags(null);
    }
  };

  if (!user || !isAdmin) return null;

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Page heading */}
      <div>
        <h1 className="text-2xl font-bold theme-text flex items-center gap-3">
          <Flag size={24} className="text-red-400" />
          {t("admin.flaggedCharactersTitle") || "Flagged Characters"}
        </h1>
        <p className="mt-2 text-base theme-text-muted">
          {t("admin.flaggedCharactersSubtitle") ||
            "Review characters that have been reported by users."}
        </p>
      </div>

      {/* Content area */}
      {isLoading ? (
        <div className="flex items-center justify-center gap-3 py-20">
          <Loader2 className="animate-spin" size={24} />
          <span className="text-base theme-text-muted">
            {t("common.loading") || "Loading..."}
          </span>
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-6 text-center text-base text-red-300">
          {error}
        </div>
      ) : characters.length === 0 ? (
        <div className="py-20 text-center">
          <Flag size={40} className="mx-auto mb-4 text-gray-500" />
          <p className="text-base theme-text-muted">
            No flagged characters at this time.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {characters.map((char) => (
            <div
              key={char.character_id}
              className="flex flex-col gap-3 rounded-2xl border p-5 theme-card"
            >
              {/* Character info */}
              <div className="min-w-0">
                <h3 className="text-base font-semibold theme-text">
                  {char.name}
                </h3>
                {char.latest_flag_reason && (
                  <p className="mt-1.5 text-sm theme-text-muted line-clamp-2">
                    <span className="font-medium text-rose-400">
                      Latest reason:
                    </span>{" "}
                    {char.latest_flag_reason}
                  </p>
                )}
              </div>

              {/* Meta row – flag count + actions */}
              <div className="flex items-center justify-between gap-2 text-sm">
                <div
                  className="flex items-center gap-1.5 rounded-full px-3 py-1"
                  style={{
                    backgroundColor: "rgba(239, 68, 68, 0.15)",
                    color: "rgb(248, 113, 113)",
                  }}
                >
                  <Flag size={14} />
                  <span className="font-semibold">{char.flag_count}</span>
                </div>

                <div className="flex items-center gap-2">
                  {/* View character */}
                  <button
                    onClick={() => navigate(`/characters/${char.character_id}`)}
                    className="rounded-xl p-2 text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
                    title="View character"
                  >
                    <Eye size={16} />
                  </button>

                  {/* Clear Flags (keep character) */}
                  <button
                    onClick={() => handleClearFlagsClick(char)}
                    className="rounded-xl p-2 text-amber-400 hover:bg-amber-500/20 hover:text-amber-300 transition"
                    title="Clear all flags for this character"
                  >
                    <ShieldOff size={16} />
                  </button>

                  {/* Delete Character (permanent) */}
                  <button
                    onClick={() => handleDeleteClick(char)}
                    className="rounded-xl p-2 text-red-400 hover:bg-red-500/20 hover:text-red-300 transition"
                    title="Delete character permanently"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Confirm Dialog: Delete Character */}
      <ConfirmDialog
        isOpen={isDeleteConfirmOpen}
        title="Delete Character"
        message="Are you sure you want to permanently delete this character? This will notify all users currently chatting with them. This action cannot be undone."
        confirmText="Delete"
        isLoading={isDeleting}
        onConfirm={handleConfirmDelete}
        onClose={handleCancelDelete}
      />

      {/* Confirm Dialog: Clear Flags */}
      <ConfirmDialog
        isOpen={isClearFlagsConfirmOpen}
        title="Clear Flags"
        message="Remove all flag reports for this character. The character will remain available and will no longer appear in the flagged list. This action can be reversed if new reports are submitted."
        confirmText="Clear Flags"
        isLoading={isClearingFlags}
        onConfirm={handleConfirmClearFlags}
        onClose={handleCancelClearFlags}
      />
    </div>
  );
}
