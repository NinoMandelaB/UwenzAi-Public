// src/hooks/useSparkPoolBalance.js
import { useSparkPoolBalanceContext } from "../context/SparkPoolBalanceContext";

/**
 * Custom hook to access the unified global spark_pool balance state.
 * Subscribes directly to the centralized SparkPoolBalanceProvider context,
 * ensuring simultaneous UI updates across components (e.g., AppHeader and ChatModal)
 * whenever a WebSocket or HTTP update is received.
 */
export const useSparkPoolBalance = () => {
  return useSparkPoolBalanceContext();
};
