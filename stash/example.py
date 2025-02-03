"""Example usage of Strawberry Stash integration."""

import asyncio
import logging

from metadata import Account, Media, Post

from .client import StashClient
from .types import Gallery, Performer, Scene, Studio


async def main():
    """Example of converting metadata to Stash."""
    # Set up logging
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger("stash.example")

    # Initialize client
    client = StashClient(
        conn={
            "Scheme": "http",
            "Host": "localhost",
            "Port": 9999,
            "ApiKey": "your_api_key",
            "Logger": logger,
        },
        verify_ssl=True,
    )

    try:
        # Example account
        account = Account(
            id=1,
            username="example",
            displayName="Example User",
            about="Example bio",
            location="US",
        )

        # Convert account to performer and studio
        performer = await Performer.from_account(account)
        await performer.save(client)
        logger.info(f"Created performer: {performer.name}")

        studio = await Studio.from_account(account)
        await studio.save(client)
        logger.info(f"Created studio: {studio.name}")

        # Example post with media
        post = Post(
            id=1,
            accountId=account.id,
            content="Example post",
            createdAt=account.createdAt,
        )

        media = Media(
            id=1,
            accountId=account.id,
            local_filename="example.mp4",
            createdAt=post.createdAt,
        )

        # Convert to scene
        scene = await Scene.from_media(
            media=media,
            post=post,
            performer=performer,
            studio=studio,
        )
        await scene.save(client)
        logger.info(f"Created scene: {scene.title}")

        # Convert post to gallery
        gallery = await Gallery.from_content(
            content=post,
            performer=performer,
            studio=studio,
        )
        await gallery.save(client)
        logger.info(f"Created gallery: {gallery.title}")

    finally:
        # Clean up client
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
