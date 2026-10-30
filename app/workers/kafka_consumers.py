import logging
from app.services.ai_pipeline import AIPipelineWorker, VernacularGeocoder

logger = logging.getLogger("trace.worker")
ai_worker = AIPipelineWorker()

async def process_report_pipeline(report_id: str, audio_uri: str, admin_lgd_code: str, db_pool):
    """
    Simulates the event-driven async workers moving a report through:
    Audio Clean -> ASR Transcription -> NER Extraction -> Tiered Geocoding -> Triage
    """
    logger.info(f"Starting pipeline execution for report_id: {report_id}")
    
    # 1. ASR Stage
    transcript_res = await ai_worker.transcribe_audio(audio_uri)
    
    # 2. Information Extraction Stage
    entities_res = await ai_worker.extract_entities(transcript_res["text_en"])
    
    # 3. Vernacular Geocoding Stage
    geocoder = VernacularGeocoder(db_pool)
    geo_res = await geocoder.resolve_location(admin_lgd_code, entities_res["anchors"])
    
    # 4. Commit results & create/update incident cluster
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Save transcription
            await conn.execute("""
                INSERT INTO transcripts (report_id, lang_codes, code_mixed, text_original, text_roman, text_en, asr_confidence, model_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, report_id, transcript_res["lang_codes"], transcript_res["code_mixed"], 
                transcript_res["text_original"], transcript_res["text_roman"], 
                transcript_res["text_en"], transcript_res["confidence"], transcript_res["model_version"])
            
            # Update report status
            await conn.execute("UPDATE reports SET status = 'GEOCODED' WHERE id = $1", report_id)
            
    logger.info(f"Pipeline completed successfully for report {report_id}")