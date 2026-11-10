// Lazily loads the same MiniLM model the compiler used, running in-browser via WebAssembly.
// Weights download once from the Hugging Face Hub and are then served from the browser cache.

const TRANSFORMERS_URL = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.5.1";
// fp32 matches the Python compiler's vectors exactly; "q8" is ~4x smaller but slightly lossy.
const MODEL_DTYPE = "fp32";

const cache = new Map();

async function createExtractor(modelId, onProgress) {
  const { pipeline } = await import(TRANSFORMERS_URL);
  return pipeline("feature-extraction", modelId, {
    dtype: MODEL_DTYPE,
    progress_callback: onProgress,
  });
}

/** Returns an async (text) => Float32Array embedding function, loading the model once per id. */
export function getEmbedder(modelId, onProgress = () => {}) {
  if (!cache.has(modelId)) {
    const pending = createExtractor(modelId, onProgress).catch((err) => {
      cache.delete(modelId); // allow a retry after a network failure
      throw err;
    });
    cache.set(modelId, pending);
  }
  return cache.get(modelId).then((extractor) => async (text) => {
    const output = await extractor(text, { pooling: "mean", normalize: true });
    return output.data;
  });
}
