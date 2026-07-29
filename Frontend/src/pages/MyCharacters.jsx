// src/pages/MyCharacters.jsx

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import MyCharacterCard from "../components/MyCharacterCard";
import PageCard from "../components/PageCard";
import DeleteCharacterDialog from "../components/DeleteCharacterDialog";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import {
  listMyCharacters,
  deleteCharacter,
} from "../services/characterService";
import { getCharacterMessage } from "../utils/characterMessageMapper";
import { getPricing, getSparkPoolBalance } from "../services/billingService";

/* ---------- Data normalisation ---------- */
function normalizeCharacter(character) {
  return {
    id: character?.id || character?.slug || character?.character_id,
    characterId: character?.character_id || null,
    slug: character?.slug || null,
    name: character?.name || "",
    role: character?.role || character?.short_description || "",
    mood: character?.mood || "",
    occupation: character?.occupation || "",
    category: character?.category || "supportive",
    status: character?.status || "online",
    popular: Boolean(character?.popular ?? character?.is_featured),
    intro: character?.intro || "",
    avatarUrl: character?.avatar_url || null,
    reviewStatus: character?.review_status || null,
    isActive: Boolean(character?.is_active),
    isFeatured: Boolean(character?.is_featured),
    nsfwEnabled: Boolean(character?.nsfw_enabled),
    primaryLanguage: character?.primary_language || "en",
    shortDescription: character?.short_description || character?.role || "",
    createdByUserId: character?.created_by?.user_id || null,
    town: character?.town || null,
    country: character?.country || null,
    age: character?.age || null,
    rejectionReason: character?.rejection_reason || null,
  };
}

export default function MyCharacters() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { isAuthenticated, isAdmin, user } = useAuth();
  const isSuperuser = Boolean(user?.is_superuser);

  // Same filters as Characters page (optional, but kept for consistency)
  const filters = [
    { key: "all", label: t("characters.all") },
    { key: "popular", label: t("characters.popular") },
    { key: "supportive", label: t("characters.supportive") },
    { key: "fun", label: t("characters.fun") },
    { key: "romantic", label: t("characters.romantic") },
  ];

  const [activeFilter, setActiveFilter] = useState("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [characters, setCharacters] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");

  // Delete dialog state
  const [characterToDelete, setCharacterToDelete] = useState(null);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isDeletingCharacter, setIsDeletingCharacter] = useState(false);
  const [deleteErrorMessage, setDeleteErrorMessage] = useState("");

  // Cost confirmation dialog state
  const [showCostDialog, setShowCostDialog] = useState(false);
  const [costData, setCostData] = useState(null);
  const [sparkPoolBalance, setSparkPoolBalance] = useState(0);
  const [isLoadingCosts, setIsLoadingCosts] = useState(false);
  const [costError, setCostError] = useState("");

  /* ---------- Fetch user's characters ---------- */
  useEffect(() => {
    let isMounted = true;
    async function fetchMyCharacters() {
      setIsLoading(true);
      setErrorMessage("");
      try {
        // Use the new endpoint for authenticated user's own characters
        const response = await listMyCharacters();
        const apiCharacters = Array.isArray(response?.data?.characters)
          ? response.data.characters
          : [];
        const normalizedCharacters = apiCharacters.map(normalizeCharacter);
        if (!isMounted) return;
        setCharacters(normalizedCharacters);
      } catch (error) {
        console.error("MyCharacters fetch error:", error);
        if (!isMounted) return;
        setCharacters([]);
        setErrorMessage(
          error?.message ||
            t("characters.loadError") ||
            "Failed to load your characters.",
        );
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }
    fetchMyCharacters();
    return () => {
      isMounted = false;
    };
  }, [t]); // no dependency on filter/search because the endpoint returns all user characters

  /* ---------- Local filtering (client-side) ---------- */
  const filteredCharacters = useMemo(() => {
    let result = characters;

    // Category filter
    if (activeFilter !== "all" && activeFilter !== "popular") {
      result = result.filter((c) => c.category === activeFilter);
    } else if (activeFilter === "popular") {
      result = result.filter((c) => c.isFeatured);
    }

    // Search term
    if (searchTerm.trim()) {
      const term = searchTerm.trim().toLowerCase();
      result = result.filter(
        (c) =>
          c.name?.toLowerCase().includes(term) ||
          c.shortDescription?.toLowerCase().includes(term) ||
          c.mood?.toLowerCase().includes(term) ||
          c.category?.toLowerCase().includes(term),
      );
    }

    return result;
  }, [characters, activeFilter, searchTerm]);

  /* ---------- Handlers ---------- */
  const handleAddCharacter = async () => {
    if (!isAuthenticated) return;

    if (isAdmin) {
      navigate("/characters/create");
      return;
    }

    setIsLoadingCosts(true);
    setCostError("");
    try {
      const [pricingRes, balanceRes] = await Promise.all([
        getPricing(),
        getSparkPoolBalance(),
      ]);
      const pricing = pricingRes.data;
      const balance = balanceRes.data?.sparks || 0;
      setCostData({
        characterCost: pricing.character_generation_cost,
        imageCost: pricing.image_generation_cost,
        cashbackAmount: pricing.cashback_public_character,
      });
      setSparkPoolBalance(balance);
      setShowCostDialog(true);
    } catch (err) {
      setCostError(t("characters.loadPricingError"));
    } finally {
      setIsLoadingCosts(false);
    }
  };

  const handleProceedToCreate = () => {
    if (sparkPoolBalance < costData?.characterCost) {
      const template = t("characters.insufficientSparks");
      const message = template.replace("{cost}", costData.characterCost);
      setCostError(message);
      return;
    }
    setShowCostDialog(false);
    navigate("/characters/create");
  };

  const handleOpenDeleteDialog = (character) => {
    if (!character?.characterId) return;
    setDeleteErrorMessage("");
    setCharacterToDelete(character);
    setIsDeleteDialogOpen(true);
  };
  const handleCloseDeleteDialog = () => {
    if (isDeletingCharacter) return;
    setIsDeleteDialogOpen(false);
    setCharacterToDelete(null);
    setDeleteErrorMessage("");
  };
  const handleConfirmDelete = async () => {
    if (!characterToDelete?.characterId) return;
    try {
      setIsDeletingCharacter(true);
      setDeleteErrorMessage("");
      await deleteCharacter(characterToDelete.characterId);
      setCharacters((prev) =>
        prev.filter((c) => c.characterId !== characterToDelete.characterId),
      );
      setIsDeleteDialogOpen(false);
      setCharacterToDelete(null);
    } catch (error) {
      setDeleteErrorMessage(
        getCharacterMessage(error, "Failed to delete character."),
      );
    } finally {
      setIsDeletingCharacter(false);
    }
  };

  const isBalanceSufficient =
    costData && sparkPoolBalance >= costData.characterCost;

  return (
    <div>
      {/* Header */}
      <section className="flex items-start justify-between gap-3 mb-5">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold theme-text">
            {t("characters.myCharacters") || "My Characters"}
          </h2>
          <p className="mt-1 text-sm theme-text-muted">
            {t("characters.myCharactersSubtitle") ||
              "View and manage all your created characters, regardless of approval status."}
          </p>
        </div>

        {isAuthenticated && (
          <button
            type="button"
            onClick={handleAddCharacter}
            className="shrink-0 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90"
            style={{
              backgroundColor: "var(--color-accent-soft)",
              color: "var(--color-accent)",
              border: "1px solid var(--color-accent-soft)",
            }}
          >
            {t("characters.createButton")}
          </button>
        )}
      </section>

      {/* Search + Filters (client-side) */}
      <section className="space-y-3 mb-5">
        <div
          className="rounded-2xl border p-3"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-card)",
          }}
        >
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder={t("characters.searchPlaceholder")}
            className="w-full bg-transparent px-2 py-0 text-sm outline-none"
            style={{ color: "var(--color-text)" }}
          />
        </div>

        <div className="flex gap-2 overflow-x-auto pb-1">
          {filters.map((filter) => {
            const isActive = activeFilter === filter.key;
            return (
              <button
                key={filter.key}
                type="button"
                onClick={() => setActiveFilter(filter.key)}
                className="whitespace-nowrap rounded-full px-3 py-2 text-xs font-medium transition hover:opacity-90"
                style={{
                  backgroundColor: isActive
                    ? "var(--color-accent)"
                    : "var(--color-soft)",
                  color: isActive ? "#082f49" : "var(--color-text-muted)",
                  border: isActive
                    ? "1px solid transparent"
                    : "1px solid var(--color-border)",
                }}
              >
                {filter.label}
              </button>
            );
          })}
        </div>
      </section>

      {/* Character Grid */}
      <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {isLoading ? (
          <PageCard className="text-center col-span-full">
            <p className="text-sm font-medium theme-text">
              {t("common.loading")}
            </p>
            <p className="mt-1 text-sm theme-text-muted">
              {t("characters.loadingSubtitle")}
            </p>
          </PageCard>
        ) : errorMessage ? (
          <PageCard className="text-center col-span-full">
            <p className="text-sm font-medium theme-text">
              {t("characters.loadFailed")}
            </p>
            <p className="mt-1 text-sm theme-text-muted">{errorMessage}</p>
          </PageCard>
        ) : filteredCharacters.length > 0 ? (
          filteredCharacters.map((character) => {
            const canDelete =
              isSuperuser ||
              (isAuthenticated && user?.user_id === character.createdByUserId);

            return (
              <MyCharacterCard
                key={character.id}
                character={character}
                isAdmin={isAdmin}
                canDelete={canDelete}
                onDelete={() => handleOpenDeleteDialog(character)}
              />
            );
          })
        ) : (
          <PageCard className="text-center col-span-full">
            <p className="text-sm font-medium theme-text">
              {t("characters.noCharacters")}
            </p>
            <p className="mt-1 text-sm theme-text-muted">
              {t("characters.noCharactersSubtitle")}
            </p>
          </PageCard>
        )}
      </section>

      {/* Cost Confirmation Dialog (same as Characters page) */}
      {showCostDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm transition-all"
          onClick={(e) => {
            if (e.target === e.currentTarget && !isLoadingCosts)
              setShowCostDialog(false);
          }}
        >
          <div
            className="w-full max-w-md rounded-2xl p-6 shadow-xl transition-all"
            style={{
              backgroundColor: "var(--color-shell)",
              border: "1px solid var(--color-border)",
            }}
          >
            <h3 className="text-xl font-semibold theme-text mb-3">
              {t("characters.costDialogTitle")}
            </h3>

            {isLoadingCosts ? (
              <div className="py-8 text-center">
                <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-current border-t-transparent theme-text-muted" />
                <p className="mt-2 text-sm theme-text-muted">
                  {t("characters.loadingPricing")}
                </p>
              </div>
            ) : costError ? (
              <>
                <p className="text-sm text-red-500 mb-4">{costError}</p>
                <button
                  onClick={() => setShowCostDialog(false)}
                  className="w-full rounded-xl bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90"
                  style={{ backgroundColor: "var(--color-accent)" }}
                >
                  {t("common.close")}
                </button>
              </>
            ) : (
              <>
                <div className="space-y-3 mb-6">
                  <div
                    className="rounded-xl p-4"
                    style={{ backgroundColor: "var(--color-card)" }}
                  >
                    <p className="text-sm theme-text-muted mb-2">
                      {t("characters.creationCostDescription").replace(
                        "{cost}",
                        costData.characterCost,
                      )}{" "}
                      {t("spark_pool.sparks")}.
                    </p>
                    <ul className="text-sm theme-text-muted list-disc list-inside space-y-1 ml-1">
                      <li>{t("characters.costIncludesProfile")}</li>
                      <li>{t("characters.costIncludesAvatar")}</li>
                    </ul>
                    <p className="text-sm theme-text-muted mt-3">
                      {t("characters.additionalImageCost").replace(
                        "{cost}",
                        costData.imageCost,
                      )}{" "}
                      {t("spark_pool.sparks")}.
                    </p>
                    <p className="text-sm theme-text-muted mt-3">
                      {t("characters.cashbackInfo").replace(
                        "{amount}",
                        costData.cashbackAmount,
                      )}
                    </p>
                    <div className="mt-3 pt-3 border-t theme-border flex justify-between text-sm">
                      <span className="theme-text-muted">
                        {t("characters.yourBalance")}
                      </span>
                      <span
                        className={`font-semibold ${
                          isBalanceSufficient
                            ? "text-green-600"
                            : "text-red-500"
                        }`}
                      >
                        {sparkPoolBalance} {t("spark_pool.sparks")}
                      </span>
                    </div>
                    {!isBalanceSufficient && (
                      <p className="text-xs text-red-500 mt-2">
                        {t("characters.insufficientSparks").replace(
                          "{cost}",
                          costData.characterCost,
                        )}
                      </p>
                    )}
                  </div>
                </div>

                <div className="flex gap-3">
                  <button
                    onClick={() => setShowCostDialog(false)}
                    className="flex-1 rounded-xl border px-4 py-2 text-sm font-medium transition hover:opacity-80"
                    style={{
                      borderColor: "var(--color-border)",
                      color: "var(--color-text-muted)",
                    }}
                  >
                    {t("common.cancel")}
                  </button>
                  <button
                    onClick={handleProceedToCreate}
                    disabled={!isBalanceSufficient}
                    className="flex-1 rounded-xl px-4 py-2 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed"
                    style={{
                      backgroundColor: "var(--color-accent)",
                      color: "#082f49",
                    }}
                  >
                    {t("characters.proceedToCreate")}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <DeleteCharacterDialog
        isOpen={isDeleteDialogOpen}
        character={characterToDelete}
        isDeleting={isDeletingCharacter}
        errorMessage={deleteErrorMessage}
        onClose={handleCloseDeleteDialog}
        onConfirm={handleConfirmDelete}
      />
    </div>
  );
}
