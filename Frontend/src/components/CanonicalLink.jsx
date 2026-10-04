import { useEffect } from "react";
import { useLocation } from "react-router-dom";

/**
 * Keeps <link rel="canonical"> in the document head in sync with the
 * active route.
 *
 * The SPA is served from a single static index.html, so a hardcoded
 * canonical in index.html would declare every page (e.g. /characters,
 * /terms) a duplicate of the homepage. Instead, this component injects
 * and updates a canonical link per route so each page self-canonicalizes.
 * Google (and Bing) pick up the rendered canonical when they render the
 * app, which consolidates the www/apex and http/https hostname variants
 * that Search Console currently tracks as separate pages.
 *
 * Query strings are intentionally dropped: canonical URLs never carry
 * tracking or filter parameters.
 */
export default function CanonicalLink() {
  const { pathname } = useLocation();

  useEffect(() => {
    const href = `${window.location.origin}${pathname}`;
    let link = document.querySelector('link[rel="canonical"]');
    if (!link) {
      link = document.createElement("link");
      link.setAttribute("rel", "canonical");
      document.head.appendChild(link);
    }
    link.setAttribute("href", href);
  }, [pathname]);

  return null;
}
