import httpx
import logging
import json

logger = logging.getLogger("trace.ai")

class AIPipelineWorker:
    def __init__(self, triton_url: str = "http://localhost:8001"):
        self.triton_url = triton_url

    async def transcribe_audio(self, audio_path: str) -> dict:
        """
        Calls self-hosted IndicWhisper model endpoint for code-mixed speech ASR.
        """
        # Inference request payload to Triton/gRPC model server
        logger.info(f"Running ASR on {audio_path}")
        # Simulated model output response format
        return {
            "lang_codes": ["hi", "en"],
            "code_mixed": True,
            "text_original": "पानी भर गया है काली मंदिर के पीछे मदद चाहिए",
            "text_roman": "pani bhar gaya hai kali mandir ke peeche madad chahiye",
            "text_en": "Water has flooded behind Kali temple, need help",
            "confidence": 0.92,
            "model_version": "indic-whisper-v1.2"
        }

    async def extract_entities(self, text_en: str) -> dict:
        """
        Executes token NER and instruction LLM extraction for structured parameters.
        """
        logger.info("Extracting entities & relations via IndicBERT & LLM")
        return {
            "urgency": 4,
            "needs": ["rescue", "water pumping"],
            "people_count": 6,
            "vulnerable_group": ["infants", "elderly"],
            "anchors": [
                {"anchor": "kali mandir", "relation": "behind", "secondary": None}
            ]
        }

class VernacularGeocoder:
    def __init__(self, db_pool):
        self.db = db_pool

    async def resolve_location(self, admin_lgd_code: str, anchors: list) -> dict:
        """
        Tiered resolver: Matches administrative boundaries, queries local PostGIS gazetteer,
        and computes spatial uncertainty polygons.
        """
        async with self.db.acquire() as conn:
            # Query gazetteer for anchor matches within the admin boundary
            query = """
                SELECT ST_AsGeoJSON(geom) as geom, name, source 
                FROM gazetteer 
                WHERE admin_lgd_code = $1 AND ($2 = ANY(aliases) OR name ILIKE $3)
                LIMIT 1;
            """
            row = await conn.fetchrow(query, admin_lgd_code, anchors[0]["anchor"], f"%{anchors[0]['anchor']}%")
            
            if row:
                return {
                    "geom": json.loads(row["geom"]),
                    "radius_m": 50.0,
                    "method": "LANDMARK"
                }
            
            # Fallback to Admin Centroid if landmark resolution fails
            admin_fallback = """
                SELECT ST_AsGeoJSON(ST_Centroid(geom)) as geom 
                FROM admin_boundaries 
                WHERE lgd_code = $1;
            """
            fallback_row = await conn.fetchrow(admin_fallback, admin_lgd_code)
            return {
                "geom": json.loads(fallback_row["geom"]) if fallback_row else None,
                "radius_m": 2500.0,
                "method": "ADMIN_CENTROID"
            }