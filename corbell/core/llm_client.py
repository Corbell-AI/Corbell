"""LLM client for Corbell — multi-provider with cloud support.

Supports local providers:
  - ``anthropic``  — requires ``anthropic>=0.25`` and ANTHROPIC_API_KEY
  - ``openai``     — requires ``openai>=1.0`` and OPENAI_API_KEY
  - ``ollama``     — requires a running Ollama server (http://localhost:11434)

And cloud-hosted providers (for enterprise teams with existing cloud commitments):
  - ``aws``   — Anthropic Claude via AWS Bedrock (boto3 + AWS credentials)
  - ``azure`` — OpenAI GPT-4 via Azure OpenAI Service (openai + Azure endpoint)
  - ``gcp``   — Anthropic Claude via GCP Vertex AI (google-cloud-aiplatform)

Token usage is automatically tracked in the provided TokenUsageTracker.
"""

from __future__ import annotations

import json
import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from corbell.core.token_tracker import TokenUsageTracker


class LLMClient:
    """Provider-agnostic LLM client for Corbell.

    **Local providers** (public API keys):

    .. code-block:: yaml

        llm:
          provider: anthropic
          model: claude-sonnet-4-6  # or any supported model: claude-opus-4-6, claude-haiku-4-5, etc.
          api_key: ${ANTHROPIC_API_KEY}

    **Cloud providers** (enterprise API keys from your cloud console):

    AWS Bedrock:

    .. code-block:: yaml

        llm:
          provider: aws
          model: us.anthropic.claude-sonnet-4-6-20250514-v1:0  # check AWS console for available IDs
          aws_region: us-east-1
          # Credentials from env: AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
          # or from ~/.aws/credentials profile

    Azure OpenAI:

    .. code-block:: yaml

        llm:
          provider: azure
          model: gpt-4o
          azure_endpoint: https://my-resource.openai.azure.com/
          azure_deployment: my-gpt4o-deployment
          azure_api_version: "2024-02-01"
          api_key: ${AZURE_OPENAI_API_KEY}

    GCP Vertex AI:

    .. code-block:: yaml

        llm:
          provider: gcp
          model: claude-sonnet-4-6@20250514  # check GCP Vertex console for available IDs
          gcp_project: my-gcp-project
          gcp_region: us-central1
          # Auth: GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
    """

    def __init__(
        self,
        provider: str = "anthropic",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        token_tracker: Optional["TokenUsageTracker"] = None,
        # Cloud provider config
        aws_region: Optional[str] = None,
        azure_endpoint: Optional[str] = None,
        azure_deployment: Optional[str] = None,
        azure_api_version: Optional[str] = None,
        gcp_project: Optional[str] = None,
        gcp_region: Optional[str] = None,
    ):
        """Initialize the LLM client.

        Args:
            provider: One of ``anthropic``, ``openai``, ``ollama``, ``aws``, ``azure``, ``gcp``.
            model: Model identifier (see defaults per provider below).
            api_key: API key. If None, resolved from environment variables.
            token_tracker: Optional :class:`~corbell.core.token_tracker.TokenUsageTracker`.
                Each API call records its token usage here.
            aws_region: AWS region for Bedrock (default: ``us-east-1``).
            azure_endpoint: Azure OpenAI resource endpoint URL.
            azure_deployment: Azure OpenAI deployment name.
            azure_api_version: Azure OpenAI API version (default: ``2024-02-01``).
            gcp_project: GCP project ID for Vertex AI.
            gcp_region: GCP region for Vertex AI (default: ``us-central1``).
        """
        self.provider = provider.lower()
        self._api_key = api_key or self._resolve_key()
        self.token_tracker = token_tracker

        # Cloud config
        self.aws_region = aws_region or os.getenv("AWS_REGION", "us-east-1")
        self.azure_endpoint = azure_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.azure_deployment = azure_deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        self.azure_api_version = azure_api_version or os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
        self.gcp_project = gcp_project or os.getenv("GCP_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", ""))
        self.gcp_region = gcp_region or os.getenv("GCP_REGION", "us-central1")

        # Stable alias defaults for providers that maintain them.
        # AWS / Azure / GCP have no safe default — model IDs are console-specific
        # and region-specific, so we require an explicit value for those.
        _defaults = {
            "anthropic": "claude-sonnet-4-6",
            "openai":    "gpt-4o",
            "ollama":    "llama3",
        }
        self.model = model or _defaults.get(self.provider)
        if not self.model:
            raise ValueError(
                f"No model specified for provider '{self.provider}'.\n"
                f"Add 'model: <your-model-id>' to workspace.yaml under the llm block.\n"
                f"Find available model IDs in your cloud console."
            )

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 8000,
        temperature: float = 0.1,
        request_type: Optional[str] = None,
    ) -> str:
        """Call the configured LLM provider.

        Args:
            system_prompt: System / persona prompt.
            user_prompt: User message / context.
            max_tokens: Max tokens in the response.
            temperature: Sampling temperature.
            request_type: Label for token tracking (e.g. ``spec_generation``).

        Returns:
            Text response string. Falls back to structured template if no credentials.
        """
        rt = request_type or "call"

        provider_map = {
            "anthropic": lambda: self._call_anthropic(system_prompt, user_prompt, max_tokens, temperature, rt),
            "openai": lambda: self._call_openai(system_prompt, user_prompt, max_tokens, temperature, rt),
            "ollama": lambda: self._call_ollama(system_prompt, user_prompt, max_tokens),
            "aws": lambda: self._call_aws_bedrock(system_prompt, user_prompt, max_tokens, temperature, rt),
            "azure": lambda: self._call_azure_openai(system_prompt, user_prompt, max_tokens, temperature, rt),
            "gcp": lambda: self._call_gcp_vertex(system_prompt, user_prompt, max_tokens, temperature, rt),
        }

        if self.provider not in provider_map:
            return self._fallback_response(system_prompt, user_prompt)

        if not self.is_configured:
            return self._fallback_response(system_prompt, user_prompt)

        try:
            return provider_map[self.provider]()
        except Exception as e:
            print(f"⚠️  LLM call failed ({self.provider}): {e}")
            return self._fallback_response(system_prompt, user_prompt)

    @property
    def is_configured(self) -> bool:
        """True if credentials are available for the configured provider."""
        if self.provider == "ollama":
            return True
        if self.provider == "aws":
            # Long-term API key (BEDROCK_API_KEY) takes priority
            if os.getenv("BEDROCK_API_KEY") or self._api_key:
                return True
            # Fall back: boto3 credential chain
            return bool(
                os.getenv("AWS_ACCESS_KEY_ID")
                or os.getenv("AWS_PROFILE")
                or os.path.exists(os.path.expanduser("~/.aws/credentials"))
            )
        if self.provider == "azure":
            return bool(self._api_key and self.azure_endpoint)
        if self.provider == "gcp":
            return bool(
                os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
                or os.getenv("GOOGLE_CLOUD_PROJECT")
                or self.gcp_project
            )
        return bool(self._api_key)

    @property
    def provider_display(self) -> str:
        """Human-readable provider description."""
        labels = {
            "anthropic": f"Anthropic ({self.model})",
            "openai": f"OpenAI ({self.model})",
            "ollama": f"Ollama/{self.model} (local)",
            "aws": f"AWS Bedrock/{self.model} @ {self.aws_region}",
            "azure": f"Azure OpenAI/{self.model} ({self.azure_deployment or 'default'})",
            "gcp": f"GCP Vertex AI/{self.model} @ {self.gcp_region}",
        }
        return labels.get(self.provider, self.provider)

    # ------------------------------------------------------------------ #
    # Provider implementations — local                                     #
    # ------------------------------------------------------------------ #

    def _call_anthropic(
        self, system: str, user: str, max_tokens: int, temperature: float,
        request_type: str = "call",
    ) -> str:
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install corbell[anthropic]")

        client = anthropic.Anthropic(api_key=self._api_key)
        msg = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        if self.token_tracker and hasattr(msg, "usage"):
            self.token_tracker.record(request_type, self.model, msg.usage.input_tokens, msg.usage.output_tokens)

        return msg.content[0].text

    def _call_openai(
        self, system: str, user: str, max_tokens: int, temperature: float,
        request_type: str = "call",
    ) -> str:
        try:
            import openai
        except ImportError:
            raise ImportError("pip install corbell[openai]")

        client = openai.OpenAI(api_key=self._api_key)
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        if self.token_tracker and resp.usage:
            self.token_tracker.record(
                request_type, self.model,
                resp.usage.prompt_tokens, resp.usage.completion_tokens,
            )

        return resp.choices[0].message.content or ""

    def _call_ollama(self, system: str, user: str, max_tokens: int) -> str:
        """Call a local Ollama instance (no token tracking — free local model)."""
        import urllib.request

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }).encode()

        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data.get("message", {}).get("content", "")

    # ------------------------------------------------------------------ #
    # Provider implementations — cloud                                     #
    # ------------------------------------------------------------------ #

    def _call_aws_bedrock(
        self, system: str, user: str, max_tokens: int, temperature: float,
        request_type: str = "call",
    ) -> str:
        """Call Anthropic Claude via AWS Bedrock.

        **Auth option 1 — Long-term API key (simplest, recommended):**
        Paste the key AWS gives you from the Bedrock console directly.

        .. code-block:: bash

            export BEDROCK_API_KEY=your-long-term-api-key
            export AWS_REGION=us-east-1   # optional, default: us-east-1

        Or in workspace.yaml:

        .. code-block:: yaml

            llm:
              provider: aws
              model: us.anthropic.claude-sonnet-4-6-20250514-v1:0  # check AWS console for available IDs
              api_key: ${BEDROCK_API_KEY}
              aws_region: us-east-1

        **Auth option 2 — IAM credential chain (boto3):**
        - Environment: ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY``
        - Profile: ``aws configure`` or ``AWS_PROFILE``
        - Instance metadata (EC2/ECS/Lambda)
        """
        # --- Long-term Bearer key path (simplest for users) ---
        bearer_key = os.getenv("BEDROCK_API_KEY") or self._api_key
        region = self.aws_region or os.getenv("AWS_REGION", "us-east-1")
        endpoint_url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{self.model}/invoke"

        if bearer_key:
            import urllib.request
            import urllib.error

            payload = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "top_p": 0.9,
            }).encode()

            req = urllib.request.Request(
                endpoint_url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {bearer_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read())

            if self.token_tracker:
                usage = result.get("usage", {})
                self.token_tracker.record(
                    request_type, self.model,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )
            content = result.get("content", [])
            if content:
                return content[0]["text"]
            raise ValueError(f"Unexpected Bedrock response: {result}")

        # --- boto3 IAM credential chain fallback ---
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "pip install corbell[aws]\n"
                "Then either:\n"
                "  Option 1 (simpler): set BEDROCK_API_KEY=<your AWS Bedrock key>\n"
                "  Option 2 (IAM):     aws configure  (or set AWS_ACCESS_KEY_ID/SECRET)"
            )

        client = boto3.client("bedrock-runtime", region_name=region)
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        })

        resp = client.invoke_model(modelId=self.model, body=body)
        result = json.loads(resp["body"].read())

        if self.token_tracker:
            usage = result.get("usage", {})
            self.token_tracker.record(
                request_type, self.model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )

        content = result.get("content", [])
        if content:
            return content[0]["text"]
        raise ValueError(f"Unexpected Bedrock response: {result}")

    def _call_azure_openai(
        self, system: str, user: str, max_tokens: int, temperature: float,
        request_type: str = "call",
    ) -> str:
        """Call GPT-4 models via Azure OpenAI Service.

        Requires:
        - AZURE_OPENAI_API_KEY (or api_key in workspace.yaml)
        - AZURE_OPENAI_ENDPOINT (e.g. https://my-resource.openai.azure.com/)
        - AZURE_OPENAI_DEPLOYMENT (your deployment name, e.g. gpt-4o-prod)

        Set these in your .env or workspace.yaml llm block.
        """
        try:
            import openai
        except ImportError:
            raise ImportError("pip install corbell[openai]")

        deployment = self.azure_deployment or self.model
        client = openai.AzureOpenAI(
            api_key=self._api_key,
            azure_endpoint=self.azure_endpoint,
            azure_deployment=deployment,
            api_version=self.azure_api_version,
        )

        resp = client.chat.completions.create(
            model=deployment,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        if self.token_tracker and resp.usage:
            self.token_tracker.record(
                request_type, f"azure/{deployment}",
                resp.usage.prompt_tokens, resp.usage.completion_tokens,
            )

        return resp.choices[0].message.content or ""

    def _call_gcp_vertex(
        self, system: str, user: str, max_tokens: int, temperature: float,
        request_type: str = "call",
    ) -> str:
        """Call Anthropic Claude via GCP Vertex AI.

        Auth options (pick one):
        1. Application Default Credentials: ``gcloud auth application-default login``
        2. Service account: set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

        Set GCP_PROJECT + GCP_REGION in .env or workspace.yaml.

        Requires: ``pip install "google-cloud-aiplatform>=1.38" anthropic[vertex]``
        """
        try:
            import anthropic
            from anthropic import AnthropicVertex  # type: ignore[attr-defined]
        except (ImportError, AttributeError):
            raise ImportError(
                "pip install anthropic[vertex] google-cloud-aiplatform\n"
                "Then authenticate: gcloud auth application-default login\n"
                "Or set GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json"
            )

        client = AnthropicVertex(
            project_id=self.gcp_project,
            region=self.gcp_region,
        )

        msg = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        if self.token_tracker and hasattr(msg, "usage"):
            self.token_tracker.record(
                request_type, f"gcp/{self.model}",
                msg.usage.input_tokens, msg.usage.output_tokens,
            )

        return msg.content[0].text

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _resolve_key(self) -> Optional[str]:
        env_map = {
            "anthropic": ["ANTHROPIC_API_KEY", "CORBELL_LLM_API_KEY"],
            "openai": ["OPENAI_API_KEY", "CORBELL_LLM_API_KEY"],
            "azure": ["AZURE_OPENAI_API_KEY", "CORBELL_LLM_API_KEY"],
            "ollama": [],
            "aws": [],   # Uses boto3 credential chain
            "gcp": [],   # Uses Google ADC
        }
        for var in env_map.get(self.provider, ["CORBELL_LLM_API_KEY"]):
            val = os.environ.get(var)
            if val:
                return val
        return None

    def _fallback_response(self, system: str, user: str) -> str:
        """Return a structured template when no LLM credentials are available."""
        if "design document" in system.lower() or "technical design" in system.lower():
            return _MOCK_DESIGN_DOC
        if "design decisions" in system.lower() or "extract" in system.lower():
            return "[]"
        if "pattern" in system.lower():
            return "{}"
        if any(kw in system.lower() for kw in ("search", "keywords", "queries")):
            import re
            sentences = [s.strip() for s in re.split(r'[.\n]', user) if len(s.strip()) > 30]
            return "\n".join(sentences[:3]) if sentences else user[:200]
        return (
            "⚠️  No LLM credentials configured.\n"
            "\n"
            "Quick setup — pick your provider:\n"
            "\n"
            "  Anthropic:   export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  OpenAI:      export OPENAI_API_KEY=sk-...\n"
            "  AWS Bedrock: export BEDROCK_API_KEY=<your-long-term-key> AWS_REGION=us-east-1\n"
            "               (or IAM): export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...\n"
            "  Azure:       export AZURE_OPENAI_API_KEY=... AZURE_OPENAI_ENDPOINT=https://...\n"
            "               export AZURE_OPENAI_DEPLOYMENT=my-gpt4o\n"
            "  GCP Vertex:  gcloud auth application-default login\n"
            "               export GCP_PROJECT=my-project GCP_REGION=us-central1\n"
            "\n"
            "Update corbell-data/workspace.yaml llm.provider accordingly, then re-run."
        )


_MOCK_DESIGN_DOC = """\
# Technical Design Document

> ⚠️ **Template mode**: No LLM credentials configured.
>
> Quick setup options:
> - Anthropic: `export ANTHROPIC_API_KEY=sk-ant-...`
> - OpenAI: `export OPENAI_API_KEY=sk-...`
> - AWS Bedrock: `export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=us-east-1`
> - Azure: `export AZURE_OPENAI_API_KEY=... AZURE_OPENAI_ENDPOINT=https://...`
> - GCP Vertex: `gcloud auth application-default login && export GCP_PROJECT=...`
>
> See README.md → LLM Providers for full instructions.

## Context

<!-- Describe WHY this feature is being built. -->

## Current Architecture

<!-- CORBELL_GRAPH_START -->
<!-- Current service graph will be inserted here by corbell. -->
<!-- CORBELL_GRAPH_END -->

## Proposed Design

### Service Changes

<!-- What changes in each service. -->

### Data Flow

<!-- Sequence or description of how data moves. -->

### Failure Modes and Mitigations

<!-- What can go wrong, how each is handled. -->

## Reliability and Risk Constraints

<!-- CORBELL_CONSTRAINTS_START -->
<!-- CORBELL_CONSTRAINTS_END -->

## Rollout Plan

<!-- Phases, feature flags, rollback plan. -->

## Open Questions

<!-- Things not yet decided. -->
"""
