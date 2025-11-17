"""
Test if Ollama is working with OG-AI
"""

print("="*70)
print("  TESTING OLLAMA WITH OG-AI")
print("="*70)
print()

# Test 1: Check if Ollama package is installed
print("📦 Step 1: Checking if Ollama package is installed...")
try:
    import ollama
    print("   ✓ Ollama package is installed!")
except ImportError:
    print("   ✗ Ollama package NOT installed!")
    print("   → Run: pip install ollama")
    exit(1)

# Test 2: Check if Ollama service is running
print("\n🔌 Step 2: Checking if Ollama service is running...")
try:
    response = ollama.list()
    print("   ✓ Ollama service is RUNNING!")
    print(f"   Available models: {len(response.get('models', []))}")
    
    # Show available models
    if response.get('models'):
        print("\n   Models you have:")
        for model in response['models']:
            print(f"      - {model['name']}")
    else:
        print("\n   ⚠️  No models installed yet!")
        print("   → Run: ollama pull llama3.2")
        
except Exception as e:
    print(f"   ✗ Ollama service is NOT running!")
    print(f"   Error: {e}")
    print("\n   To fix:")
    print("   1. Download Ollama from https://ollama.ai")
    print("   2. Install it")
    print("   3. Ollama will run in background automatically")
    print("   4. Or manually run 'ollama serve' in a terminal")
    exit(1)

# Test 3: Try to use llama3.2
print("\n🤖 Step 3: Testing llama3.2 model...")
try:
    test_response = ollama.chat(
        model='llama3.2',
        messages=[
            {'role': 'system', 'content': 'You are a test AI. Respond with just "Working!" and nothing else.'},
            {'role': 'user', 'content': 'Test'}
        ]
    )
    
    response_text = test_response['message']['content']
    print(f"   ✓ llama3.2 is working!")
    print(f"   Response: {response_text[:100]}")
    
except Exception as e:
    print(f"   ✗ llama3.2 model failed: {e}")
    print("\n   To fix:")
    print("   → Run: ollama pull llama3.2")
    exit(1)

# Test 4: Test with OG-AI Enhanced Agent
print("\n🔥 Step 4: Testing OG-AI Enhanced Agent with Ollama...")
try:
    from ai_agent_enhanced import EnhancedAIAgent
    
    agent = EnhancedAIAgent(name="OG-AI")
    response = agent.process_message("yo what's good")
    
    print("   ✓ OG-AI Enhanced Agent is WORKING with Ollama!")
    print(f"\n   Agent response:")
    print(f"   {response[:200]}...")
    
    # Check if it has the gangster personality
    has_personality = any(word in response.lower() for word in ['fuck', 'shit', 'yo', 'aight', 'bet', 'damn'])
    if has_personality:
        print("\n   🔥 HELL YEAH! Gangster personality is ACTIVE!")
    else:
        print("\n   ⚠️  Hmm, personality might be weak. Check your .env settings.")
    
except Exception as e:
    print(f"   ✗ OG-AI Enhanced Agent failed: {e}")
    print("\n   This might mean:")
    print("   - Some Python packages are missing")
    print("   - Run: pip install -r requirements.txt")

print("\n" + "="*70)
print("  TEST COMPLETE!")
print("="*70)
print("\n✅ Everything is working! You're ready to run:")
print("   python app.py")
print("\nThen open: http://localhost:8000")
print()
