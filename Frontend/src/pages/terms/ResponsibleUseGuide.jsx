// src/pages/terms/ResponsibleUseGuide.jsx

import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";

export default function ResponsibleUseGuide() {
  const navigate = useNavigate();

  return (
    <div className="space-y-6">
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-2 text-sm font-medium theme-text-muted hover:underline"
      >
        <ArrowLeft size={16} />
        Back
      </button>

      <div className="max-w-prose mx-auto space-y-6 text-sm theme-text leading-relaxed">
        <h1 className="text-2xl font-semibold theme-text">
          UwenzAi Responsible Use Guide
        </h1>

        <p>
          UwenzAi is designed to be a supportive, fun AI companion. It can feel
          personal and emotionally engaging, but it is still software and should
          be used responsibly.
        </p>

        <Section title="1. Adults only">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              You must be considered an adult in the place where you use
              UwenzAi.
            </li>
            <li>
              In all cases, you must be at least 18 years old to use the
              platform.
            </li>
          </ul>
        </Section>

        <Section title="2. Content awareness and boundaries">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              UwenzAi may support some suggestive or mild NSFW image content,
              but it does not support explicit nude or pornographic image
              generation.
            </li>
            <li>
              UwenzAi includes NSFW chat features where AI companions can
              generate sexual or mature content.
            </li>
            <li>
              This content may be offensive, inappropriate, or uncomfortable for
              some users. If you find such content objectionable, please do not
              use these features or the platform.
            </li>
            <li>
              Despite technical safeguards, the AI may occasionally produce
              offensive, harmful, or illegal content.
            </li>
          </ul>

          <p>If this happens:</p>
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Please stop the conversation immediately or steer it in a
              different direction.
            </li>
            <li>
              Avoid continuing or deepening conversations that produce harmful
              content.
            </li>
            <li>
              We ask that you not attempt to bypass safety measures or encourage
              the generation of harmful content.
            </li>
          </ul>

          <p>
            Deliberate misuse of the platform to generate illegal content may
            result in access being removed.
          </p>
        </Section>

        <Section title="3. AI is not a replacement for real people">
          <ul className="list-disc pl-5 space-y-1">
            <li>AI companions can feel real, but they are not human.</li>
            <li>
              Try not to replace real conversations, friendships, relationships,
              or community with chatbot interactions.
            </li>
            <li>
              While AI can provide companionship, meaningful human connection
              remains essential for your wellbeing.
            </li>
          </ul>
        </Section>

        <Section title="4. Protect your privacy">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              We recommend not sharing sensitive personal information with a
              chatbot.
            </li>
            <li>
              Avoid sharing things like your full name, home address, phone
              number, passwords, banking details, ID numbers, or private
              information about other people.
            </li>
            <li>
              Remember that conversations may be stored and processed as part of
              the service.
            </li>
          </ul>
        </Section>

        <Section title="5. AI can be wrong">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              AI can make mistakes, misunderstand you, or confidently say things
              that are not true.
            </li>
            <li>
              If you need medical, legal, financial, or other professional
              advice, please speak to a qualified human expert.
            </li>
            <li>
              The AI does not have real-world awareness and cannot verify
              current events or personalized circumstances.
            </li>
          </ul>
        </Section>

        <Section title="6. Take breaks">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              It is healthy to take breaks from chatting with an AI companion.
            </li>
            <li>
              If you feel like you are spending too much time with the chatbot,
              consider stepping away, resting, and reconnecting with real life.
            </li>
            <li>
              Balance is important—aim to ensure the platform enhances rather
              than replaces your daily activities.
            </li>
          </ul>
        </Section>

        <Section title="7. Talk to real people about your feelings">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              It is okay to talk to an AI companion, but emotional support
              should not come only from a chatbot.
            </li>
            <li>
              Please share your feelings and concerns with real friends, family,
              or trusted people in your life.
            </li>
            <li>
              If you are experiencing serious emotional difficulties, we
              encourage you to seek professional help.
            </li>
          </ul>
        </Section>

        <Section title="8. Mental health and crisis support">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              If you are experiencing psychological distress, depression,
              suicidal thoughts, or any mental health crisis, please reach out
              to a human professional immediately.
            </li>
            <li>
              AI companions cannot provide clinical mental health support or
              crisis intervention.
            </li>
            <li>
              Free, confidential help is available 24/7. Find a helpline in your
              country at{" "}
              <a
                href="https://findahelpline.com/"
                className="underline text-[var(--color-accent)]"
                target="_blank"
                rel="noreferrer"
              >
                https://findahelpline.com/
              </a>
              .
            </li>
            <li>
              You are not alone, and there are always people ready to help.
            </li>
          </ul>
        </Section>

        <Section title="9. Avoid illegal or harmful activities">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Please do not use UwenzAi for illegal activity, abuse, harassment,
              exploitation, fraud, or anything that could harm yourself or
              others.
            </li>
            <li>
              This includes attempting to generate illegal content, coordinating
              harmful activities, or using the platform to manipulate or exploit
              others.
            </li>
            <li>
              If the platform is used in a dangerous or abusive way, access may
              be limited or removed.
            </li>
          </ul>
        </Section>

        <Section title="10. Spend responsibly">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              UwenzAi includes paid features, subscriptions, or virtual
              currency, please spend responsibly.
            </li>
            <li>
              Avoid spending money you cannot afford to lose, and try not to let
              chatbot spending interfere with essentials like food, rent,
              school, or health.
            </li>
            <li>
              Consider setting personal spending limits and being mindful of
              subscription renewals.
            </li>
          </ul>
        </Section>

        <Section title="11. Keep your account secure">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Please do not share your account, password, or login information
              with anyone.
            </li>
            <li>
              If you think someone else accessed your account, change your
              password as soon as possible.
            </li>
            <li>
              Enable any available security features to protect your account.
            </li>
          </ul>
        </Section>

        <Section title="12. Use your judgment">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Treat AI responses as generated content, not as facts or
              intentions from a real person.
            </li>
            <li>
              Stay aware, think critically, and use UwenzAi in a balanced way.
            </li>
            <li>
              Remember that the AI has no consciousness, emotions, or personal
              investment in your wellbeing—it simulates conversation patterns.
            </li>
          </ul>
        </Section>

        <Section title="13. No real person impersonation">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Do not create AI characters that depict, impersonate, or are based
              on real people.
            </li>
            <li>
              This applies to all real individuals, including celebrities,
              public figures, acquaintances, and private individuals.
            </li>
            <li>
              Characters should be fictional and not designed to replicate the
              identity, likeness, or persona of any actual living person.
            </li>
          </ul>
        </Section>

        <Section title="14. Respect the platform">
          <p>
            Do not attempt to hack, exploit, or manipulate the UwenzAi platform.
          </p>

          <p>This includes:</p>
          <ul className="list-disc pl-5 space-y-1">
            <li>Attempting to disrupt the service or take it offline.</li>
            <li>Trying to access paid features without proper payment.</li>
            <li>
              Attempting to bypass safety features, content filters, or usage
              limits.
            </li>
            <li>Exploiting bugs or vulnerabilities for personal gain.</li>
            <li>
              Reverse engineering or attempting to extract proprietary systems.
            </li>
          </ul>

          <p>
            Actions that compromise the platform&apos;s security or integrity
            may result in account termination.
          </p>
          <p>
            If you discover a security vulnerability, we appreciate you
            reporting it responsibly to our team.
          </p>
        </Section>

        <Section title="15. Using your own API key (BYOK)">
          <p>
            If you connect your own third-party AI API through the BYOK feature
            in Settings, please follow these guidelines to protect yourself and
            others.
          </p>

          <p>
            <strong>Protect your API key</strong>
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Use a temporary or restricted API key whenever your provider
              offers that option. Many providers (such as OpenAI, Anthropic, and
              others) let you create keys with limited permissions or expiry
              dates — use those instead of your main account key.
            </li>
            <li>
              Never share your API key with anyone, including in chat messages,
              system prompts, screenshots, or support requests.
            </li>
            <li>
              Set a spending limit or usage quota at your API vendor to cap how
              much the key can spend. This protects you if the key is ever
              exposed or misused.
            </li>
            <li>
              If you suspect your API key has been compromised, revoke it at
              your provider immediately and generate a new one.
            </li>
          </ul>

          <p>
            <strong>Understand the costs</strong>
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Using BYOK does not remove the UwenzAi platform usage cost — a
              reduced spark fee still applies per message pair to cover platform
              infrastructure, memory, and other services.
            </li>
            <li>
              Any API costs charged by your third-party provider are entirely
              your own responsibility. UwenzAi has no control over or visibility
              into what your provider charges and will not reimburse those costs
              under any circumstances.
            </li>
            <li>
              Monitor your usage at your API vendor regularly, especially if you
              are using a high-volume or high-cost model.
            </li>
          </ul>

          <p>
            <strong>Follow platform rules for system prompts</strong>
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Custom system prompts must comply with UwenzAi's Terms of Use and
              content policies. You may not use a system prompt to instruct the
              AI to produce content that would otherwise be prohibited on the
              platform.
            </li>
            <li>
              System prompts are stored in clear text and may be reviewed by
              UwenzAi for moderation. Do not put passwords, API keys, payment
              details, or other sensitive credentials inside your system prompt.
            </li>
            <li>
              Remember: UwenzAi's content safety layer remains active even when
              BYOK is enabled. System prompts that attempt to disable or
              circumvent safety measures are not permitted.
            </li>
          </ul>

          <p>
            <strong>Accept the limits of third-party APIs</strong>
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>
              UwenzAi cannot guarantee the availability, accuracy, or
              reliability of any external API you connect. Outages, errors, or
              degraded performance from your provider are outside our control.
            </li>
            <li>
              Loss of credentials or conversation data due to API failures is
              your own responsibility. Keep backups of any information you
              cannot afford to lose.
            </li>
          </ul>
        </Section>

        <Section title="16. Sparks">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Sparks are prepaid access units for UwenzAi. They are not mobile
              money, not e-money, and not a wallet or bank account.
            </li>
            <li>
              Sparks cannot be withdrawn to M-Pesa or any mobile money account,
              converted to cash, or transferred to other users.
            </li>
            <li>
              Sparks work only inside UwenzAi (a closed-loop system). You buy
              them to access features on the platform, and that is the only
              place they can be used.
            </li>
          </ul>
        </Section>

        <Section title="17. Interactive Chat">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Interactive Chat lets a character initiate and send you a message
              after a period of inactivity, so the conversation can continue
              naturally without you having to write first.
            </li>
            <li>
              This feature is activated per character and is deactivated by
              default. You choose which characters (if any) can reach out to
              you.
            </li>
            <li>
              Interactive Chat uses Sparks in exactly the same way and amount as
              normal chatting. Every proactive message a character sends
              consumes Sparks just like a message in a normal chat.
            </li>
            <li>
              You are responsible for managing this setting. UwenzAi is not
              liable if the feature sends messages you did not want, if the AI
              makes mistakes in the messages it initiates, or if you forget to
              deactivate the feature and lose Sparks as a result.
            </li>
            <li>
              If you enable Interactive Chat, please make sure you have enough
              Sparks for the messages it may send, and deactivate it for a
              character whenever you no longer want that character to reach out
              to you.
            </li>
          </ul>
        </Section>

        <p className="mt-6 text-sm theme-text italic">
          By using UwenzAi, you acknowledge that you understand these guidelines
          and agree to use the platform responsibly, legally, and safely.
        </p>

        <p className="mt-10 text-xs text-center theme-text-muted">
          Need help? Contact us at{" "}
          <a
            href="mailto:info@uwenzai.com"
            className="underline text-[var(--color-accent)]"
          >
            info@uwenzai.com
          </a>
          . UwenzAi Limited – Nairobi, Kenya.
        </p>
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <section>
      <h2 className="text-lg font-semibold theme-text mt-6 mb-2">{title}</h2>
      <div className="space-y-2">{children}</div>
    </section>
  );
}
