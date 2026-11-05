# Runtime graphics

These files are local copies of official provider marks for offline dashboard rendering.

| Asset | Source | Notes |
| --- | --- | --- |
| `deepseek.svg` | [DeepSeek-LLM official repository](https://github.com/deepseek-ai/DeepSeek-LLM/blob/main/images/logo.svg) ([raw SVG](https://raw.githubusercontent.com/deepseek-ai/DeepSeek-LLM/main/images/logo.svg)) | Original SVG retained unchanged. |
| `deepseek.ico` | [DeepSeek official site favicon](https://www.deepseek.com/favicon.ico) | Original ICO retained unchanged; its available frame is 225 × 225 pixels. |
| `deepseek.png` | Converted from `deepseek.ico` | Exported with Pillow at the ICO's native 225 × 225 size, preserving RGBA colors and transparency. |
| `chatgpt.png` | [OpenAI Agents SDK official repository logo SVG](https://raw.githubusercontent.com/openai/openai-agents-python/main/docs/assets/logo.svg); see [OpenAI brand guidelines](https://openai.com/brand/) | White Blossom mark with transparent background, rasterized by the bundled `sharp` tool to 256 × 256 PNG; shape and color unchanged. Used to identify ChatGPT, not as this app's product mark or as an endorsement. |
| `glm.png` | [GLM / Zhipu official site icon](https://bigmodel.cn/static/images/favicon.png) ([site](https://bigmodel.cn)) | Original 90 × 90 PNG converted with Pillow to RGBA PNG; dimensions and colors unchanged. |
| `google.png` | [Google official product icon CDN](https://www.gstatic.com/images/branding/product/2x/googleg_48dp.png) | Original 96 × 96 Google G PNG converted with Pillow to RGBA PNG; dimensions and colors unchanged. |

Retrieved on 2026-10-15. No logo artwork was redrawn, reshaped, or recolored.

## Animated status illustrations

| Asset | Agent | Character direction |
| --- | --- | --- |
| `mascot_chatgpt.png` | Codex / ChatGPT | White/lavender-haired dragon girl, pale horns, wings and tail. |
| `mascot_glm.png` | ZCode / GLM | Black-haired scholar with dark clothing, fox ears and a book. |
| `mascot_gemini.png` | Antigravity / Gemini | Blue/violet-haired cat girl, heterochromatic eyes and star ornaments. |

Created on 2026-11-05 with the built-in OpenAI image generator, using a user-supplied character illustration as visual reference. These are community-style fan illustrations, not official provider mascots. The original reference illustration is not included in this repository or the application.

Each PNG contains a 4-column by 3-row atlas: idle, working and busy, four frames per state. In v0.8.1 the original generated artwork was mechanically resized to a 768 x 576 RGBA atlas (192 pixels per frame) for sharper large portraits; transparency and cell alignment were preserved. Prompt directions and runtime behavior are recorded in [MASCOT-ANIMATIONS.md](../../docs/MASCOT-ANIMATIONS.md).

The three generated sprite atlases are supplied under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), attributed to KV Agent Desk contributors. This artwork license does not grant rights to underlying third-party character designs or provider trademarks, which remain with their respective owners. The application's source code retains its MIT license.
