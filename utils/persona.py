# utils/persona.py

import os
import json
from dotenv import load_dotenv
from typing import Dict, List

def list_available_personas(assets_dir='assets/personality') -> List[Dict]:
    """List all available personas from JSON files in the personality directory"""
    personas = []
    # Create directory if it doesn't exist
    os.makedirs(assets_dir, exist_ok=True)
    
    for filename in os.listdir(assets_dir):
        if filename.endswith('.json'):
            try:
                with open(os.path.join(assets_dir, filename), 'r', encoding='utf-8') as f:
                    persona_data = json.load(f)
                    personas.append({
                        'filename': filename,
                        'name': persona_data.get('name', 'Unknown'),
                        'description': persona_data.get('description', 'No description available')
                    })
            except json.JSONDecodeError:
                print(f"Warning: Could not parse {filename} as JSON")
            except Exception as e:
                print(f"Warning: Error reading {filename}: {str(e)}")
    
    return personas

def select_persona() -> str:
    """Interactive persona selection if no default is set"""
    personas = list_available_personas()
    
    if not personas:
        raise RuntimeError("No persona files found in assets/personality directory!")
    
    print("\nAvailable Personas:")
    for idx, persona in enumerate(personas, 1):
        print(f"\n{idx}. {persona['name']}")
        print(f"   {persona['description']}")
    
    while True:
        try:
            choice = input("\nSelect persona number (or press Enter for default): ").strip()
            if not choice:  # Empty input, use default
                break
            
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(personas):
                return personas[choice_idx]['filename']
            else:
                print("Invalid selection. Please try again.")
        except ValueError:
            print("Please enter a valid number.")

def validate_persona(persona: Dict) -> bool:
    """Validate required persona fields"""
    required_fields = ['name', 'description', 'system_prompt']
    return all(field in persona for field in required_fields)

def load_persona(filename=None, assets_dir='assets/personality') -> Dict:
    """Load persona from JSON file, with environment variable fallback"""
    # Load environment variables
    load_dotenv()
    
    # If no filename provided, check .env or ask user
    if not filename:
        filename = os.getenv('PERSONA_FILE')
        if not filename:
            filename = select_persona()
    
    # Ensure .json extension
    if not filename.endswith('.json'):
        filename += '.json'
    
    persona_path = os.path.join(assets_dir, filename)
    
    # Check if file exists
    if not os.path.isfile(persona_path):
        available = list_available_personas()
        print(f"\nWarning: Persona file '{filename}' not found!")
        print("Available personas:", [p['filename'] for p in available])
        
        if available:
            print(f"\nFalling back to first available persona: {available[0]['name']}")
            persona_path = os.path.join(assets_dir, available[0]['filename'])
        else:
            raise FileNotFoundError("No persona files available!")
    
    try:
        with open(persona_path, 'r', encoding='utf-8') as f:
            persona = json.load(f)
            
            # Validate persona
            if not validate_persona(persona):
                raise ValueError("Invalid persona format: missing required fields")
                
            print(f"\nLoaded persona: {persona.get('name', 'Unknown')}")
            return persona
            
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Error parsing persona file: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Error loading persona file: {str(e)}")
