# import os
# from dotenv import load_dotenv
# load_dotenv()
import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai import LLMExtractionStrategy
from pydantic import BaseModel, Field
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

class OpenAIModelFee(BaseModel):
    actor_name: str = Field(..., description="Name of the actor, organisation, institution, company, investor, partner, government body, or person mentioned")
    actor_type: str = Field(..., description="Type of actor: company, startup, university, research institute, government body, investor, civil society organisation, individual, or other")
    helix_category: str = Field(..., description="Quadruple Helix category: industry, academia, government, civil society, or unknown")
    role_in_ecosystem: str = Field(..., description="What role this actor plays in the quantum/deep-tech ecosystem, based only on the webpage")
    relationship_to_domain: str = Field(..., description="How this actor is connected to the domain's company, e.g. partner, investor, supplier, customer, collaborator, regulator, founder, employee, mentioned actor, or unknown")
    technology_area: str = Field(..., description="Relevant technology area, e.g. quantum computing, photonics, semiconductors, cryogenics, manufacturing, policy, investment, or unknown")
    evidence: str = Field(..., description="Short quote or close paraphrase from the webpage supporting this extraction")
    source_url: str = Field(..., description="URL of the webpage where this information was found")

async def main():
    browser_config = BrowserConfig(verbose=True)
    run_config = CrawlerRunConfig(
        word_count_threshold=20,
        extraction_strategy=LLMExtractionStrategy(
            llm_config=LLMConfig(
                provider="ollama/qwen2.5:3b",
                api_token="no-token",
                base_url="http://localhost:11434"
            ),
            schema=OpenAIModelFee.model_json_schema(),
            extraction_type="schema",
            instruction="""
            You are extracting actors and ecosystem relationships for a research project on quantum and deep-tech innovation networks.

            From the crawled webpage, extract all relevant actors mentioned in relation to domain's company or the quantum/deep-tech ecosystem.

            Actors may include:
            - companies
            - startups
            - universities
            - research institutes
            - government bodies
            - investors
            - suppliers
            - partners
            - customers
            - founders, executives, researchers, or other individuals
            - civil society or nonprofit organisations

            For each actor, return one JSON object matching this schema:
            {
            "actor_name": "...",
            "actor_type": "...",
            "helix_category": "...",
            "role_in_ecosystem": "...",
            "relationship_to_domain": "...",
            "technology_area": "...",
            "evidence": "...",
            "source_url": "...",
            }

            Rules:
            - Use only text that appears on this webpage.
            - Do not infer partnerships.
            - Do not invent news URLs.
            - Extract multiple actors if the page mentions multiple relevant organisations or people.
            - Do not include actors unless there is direct evidence in the page text.
            - The evidence field must quote or closely copy the exact webpage text.
            - If no actors are found, return [].
            - Return only valid JSON.
            - Do not include markdown.
            - Do not include explanations.
            - If the actor is the original domain's company itself, set relationship_to_domain to "self".
            """
        ),
        cache_mode=CacheMode.BYPASS,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=3,
            max_pages=3,
            include_external=False
        )
    )
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun(
        url="https://quantumcomputinginc.com/",
        config=run_config
        )

        for result in results:
            print("\nURL:", result.url)
            print(result.extracted_content)

if __name__ == "__main__":
    asyncio.run(main())
