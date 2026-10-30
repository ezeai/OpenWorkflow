# Open Weights Policy

1. **Local path = default.** Every starter workflow runs on local ComfyUI with locally stored weights. Generation makes no network calls. The only network use is the optional download, which needs confirmation.
2. **No partner nodes or hosted inference** in the Open Weights Only path. Remote integrations, if added later, must be separate, off by default, need per-use consent, and be labelled "Remote" in the UI and exports.
3. **Three separate license records** per project: (a) Open Workflow code license (MIT), (b) each model's weights license, (c) custom nodes and other dependencies.
4. **Eligibility is computed, not asserted.** The `open_weight_eligibility` field is one of:
   - `not_eligible`: the license is restricted, or the catalogue policy says so.
   - `review`: anything else short of eligible. This is the default.
   - `eligible`: only when the model license is verified, every component's license is verified, and the catalogue policy is `eligible`. This requires a workflow review, which has not been done for any model. **No model is eligible today.**
5. **"Open weights" does not mean "commercial use allowed".** The registry records commercial restrictions and shows them before use.
6. **Identity is not license.** A verified license on a publisher page does not license a third-party conversion (GGUF, FP8 repack) or an auxiliary file. Each component carries its own status.
7. **No variant inherits a license from another variant.** The FLUX.2 4B license does not apply to the 9B. Qwen-Image-2.1 is restricted even though earlier Qwen-Image releases were Apache-2.0.
8. Exported projects will carry `open_weights_path: true|false` from the models actually used (not implemented yet; Milestone 8).
9. No weights are bundled in an installer. Downloads need explicit consent and a verified source.
10. Models installed on the development machine are not assumed eligible. See `MODEL_LICENSE_REVIEW.md`.
