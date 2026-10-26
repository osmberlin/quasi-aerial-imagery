/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** XYZ template of the 2026 aerial background. Optional: the option is hidden when empty. Contains a token, never log it. */
  readonly VITE_TILDA_2026_URL?: string
  /** XYZ template of the 2025 aerial background. Optional: the option is hidden when empty. Contains a token, never log it. */
  readonly VITE_TILDA_2025_URL?: string
}
