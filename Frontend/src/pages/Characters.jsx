// src/pages/Characters.jsx

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import CharacterCard from "../components/CharacterCard";
import MyCharacterCard from "../components/MyCharacterCard"; // <-- added for "my" tab
import PageCard from "../components/PageCard";
import DeleteCharacterDialog from "../components/DeleteCharacterDialog";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import {
  listCharacters,
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
    createdByAdmin: Boolean(character?.created_by_admin),
    town: character?.town || null,
    country: character?.country || null,
    age: character?.age || null,
  };
}

export default function Characters() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { isAuthenticated, isAdmin, user } = useAuth();
  const isSuperuser = Boolean(user?.is_superuser);

  // ── Tabs ──
  const [activeTab, setActiveTab] = useState("all");

  // ── Filters ──
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

  /* ---------- Fetch characters ---------- */
  useEffect(() => {
    let isMounted = true;

    async function fetchCharacters() {
      setIsLoading(true);
      setErrorMessage("");

      try {
        let response;

        if (activeTab === "my") {
          // Fetch only the current user's characters
          if (!isAuthenticated) {
            setCharacters([]);
            setErrorMessage(
              t("characters.loginRequired") ||
                "Please log in to see your characters.",
            );
            setIsLoading(false);
            return;
          }
          response = await listMyCharacters();
          const apiCharacters = Array.isArray(response?.data?.characters)
            ? response.data.characters
            : [];
          const normalized = apiCharacters.map(normalizeCharacter);
          if (!isMounted) return;
          setCharacters(normalized);
        } else {
          // Public characters with filters
          const params = {
            popular: activeFilter === "popular" ? true : undefined,
            category:
              activeFilter !== "all" && activeFilter !== "popular"
                ? activeFilter
                : undefined,
            search: searchTerm.trim() || undefined,
          };
          response = await listCharacters(params);
          const apiCharacters = Array.isArray(response?.data?.characters)
            ? response.data.characters
            : [];
          const normalized = apiCharacters.map(normalizeCharacter);
          if (!isMounted) return;
          setCharacters(normalized);
        }
      } catch (error) {
        console.error("Characters fetch error:", error);
        if (!isMounted) return;
        setCharacters([]);
        setErrorMessage(
          error?.message ||
            t("characters.loadError") ||
            "Failed to load characters.",
        );
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }

    fetchCharacters();
    return () => {
      isMounted = false;
    };
  }, [activeTab, activeFilter, searchTerm, t, isAuthenticated]);

  /* ---------- Filtering (client-side) ---------- */
  const filteredCharacters = useMemo(() => {
    let result = characters;

    // ── Tab filter (only for public tabs) ──
    if (activeTab === "official") {
      result = result.filter((c) => c.createdByAdmin === true);
    } else if (activeTab === "community") {
      result = result.filter((c) => c.createdByAdmin === false);
    }
    // "all" and "my" – no additional filter based on createdByAdmin

    // ── Category filter ──
    if (activeFilter !== "all" && activeFilter !== "popular") {
      result = result.filter((c) => c.category === activeFilter);
    } else if (activeFilter === "popular") {
      result = result.filter((c) => c.isFeatured);
    }

    // ── Search ──
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
  }, [characters, activeTab, activeFilter, searchTerm]);

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

  // ── Tabs configuration ──
  const tabs = [
    { key: "all", label: t("characters.tabAll") || "All" },
    { key: "official", label: t("characters.tabOfficial") || "Official" },
    { key: "community", label: t("characters.tabCommunity") || "Community" },
  ];
  if (isAuthenticated) {
    tabs.push({ key: "my", label: t("characters.tabMy") || "My Characters" });
  }

  return (
    <div>
      {/* Header */}
      <section className="flex items-start justify-between gap-3 mb-5">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold theme-text">
            {t("characters.title")}
          </h2>
          <p className="mt-1 text-sm theme-text-muted">
            {activeTab === "my"
              ? t("characters.myCharactersSubtitle") ||
                "View and manage all your created characters."
              : t("characters.subtitle")}
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

      {/* ── TAB BAR ── */}
      <div
        className="flex gap-2 border-b pb-2 mb-3 overflow-x-auto"
        style={{ borderColor: "var(--color-border)" }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-1.5 text-sm font-medium rounded-full transition whitespace-nowrap ${
              activeTab === tab.key
                ? "bg-[var(--color-accent)] text-white"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-soft)]"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Search + Filters */}
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

            // Use MyCharacterCard for "my" tab, otherwise CharacterCard
            const CardComponent =
              activeTab === "my" ? MyCharacterCard : CharacterCard;

            return (
              <CardComponent
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
              {activeTab === "my"
                ? t("characters.noMyCharacters") ||
                  "You haven't created any characters yet."
                : t("characters.noCharacters")}
            </p>
            <p className="mt-1 text-sm theme-text-muted">
              {activeTab === "my"
                ? t("characters.noMyCharactersSubtitle") ||
                  "Create your first character now!"
                : t("characters.noCharactersSubtitle")}
            </p>
          </PageCard>
        )}
      </section>

      {/* Cost Dialog */}
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
