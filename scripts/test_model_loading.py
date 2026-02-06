#!/usr/bin/env python3
"""Test script to verify model loading with fallback logic"""

import sys
import logging
from llm.manager import _load_model_with_fallback

# Configure logging to see detailed output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    logger.info("Starting model loading test...")
    
    # Try to load model with fallback
    model, model_name = _load_model_with_fallback()
    
    if model is not None and model_name is not None:
        logger.info(f"✅ SUCCESS: Loaded model '{model_name}'")
        
        # Test a simple inference
        test_prompt = "Hello, how are you?"
        logger.info(f"Testing inference with prompt: '{test_prompt}'")
        
        try:
            result = model(test_prompt, max_tokens=20)
            logger.info(f"✅ Inference test passed!")
            logger.info(f"Response: {result}")
        except Exception as e:
            logger.error(f"❌ Inference test failed: {e}")
            sys.exit(1)
    else:
        logger.error("❌ FAILED: Could not load any model")
        sys.exit(1)
    
    logger.info("All tests passed!")

if __name__ == "__main__":
    main()
