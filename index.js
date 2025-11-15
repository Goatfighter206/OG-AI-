const express = require('express');
const { OpenAI } = require('openai');
const path = require('path');

const app = express();
const port = 3000;

// --- IMPORTANT ---
// PASTE YOUR OPENAI API KEY HERE
const apiKey = "your_api_key_here";
// --- IMPORTANT ---

if (apiKey === "your_api_key_here") {
    console.error("Please replace 'your_api_key_here' with your actual OpenAI API key in index.js");
    process.exit(1);
}

const openai = new OpenAI({ apiKey });

app.use(express.json());
app.use(express.static(__dirname)); // Serve static files like index.html

// Serve the main page
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

app.post('/prompt', async (req, res) => {
  const { prompt } = req.body;

  try {
    const response = await openai.chat.completions.create({
      model: 'gpt-3.5-turbo',
      messages: [{ role: 'user', content: prompt }],
    });

    res.json(response.choices[0].message);
  } catch (error) {
    console.error(error);
    res.status(500).json({ error: 'An error occurred. Check your API key and OpenAI plan.' });
  }
});

app.listen(port, () => {
  console.log(`Server is running. Open http://localhost:${port} in your browser.`);
});
