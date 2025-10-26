# Fansly to Stash Performer Mapping

## Single Account → Single Performer

Basic mapping for a standalone account:

```python
performer = Performer(
    id="new",
    name=account.displayName or account.username,
    aliases=[account.username],  # Original username always in aliases
    details=account.about,
    urls=[f"https://fansly.com/{account.username}/posts"],
    country=account.location,
)
```

## Single Account → Multiple Performers

When multiple performers share an account (e.g., couples, groups):

### Primary Performer

```python
primary = Performer(
    id="new",
    name=account.displayName or "Primary Name",  # Personal name if known
    disambiguation=account.username,  # Shows which account they're from
    aliases=[
        account.username,  # Original account name
        "display name variations",  # Any known variations
    ],
    urls=[
        f"https://fansly.com/{account.username}/posts",
        # Additional personal social links if known
    ],
    details=account.about,  # Can include personal details
)
```

### Secondary Performer(s)

```python
secondary = Performer(
    id="new",
    name="Personal Name",  # Their individual name
    disambiguation=account.username,  # Same as primary to show relationship
    aliases=[account.username],  # Include shared account name
    urls=[
        f"https://fansly.com/{account.username}/posts",
        # Their personal links if known
    ],
    details="Role or relationship description",
)
```

## Field Usage Guidelines

### Name Field

- Use personal/stage name when known
- Fall back to username if no display name
- Should be unique among performers
- Can be changed if better name discovered

### Disambiguation Field

- Used to show account relationship
- Same value for all performers from same account
- Helps identify related performers
- Usually the account username

### Aliases List

- Always include original username
- Include known variations
- Include display names if different
- Shared across account performers
- Used for searching/matching

### URLs List

- Primary platform URL always included
- Can include personal social links
- Same account URL can be on multiple performers
- Helps track performer across platforms

### Details Field

- Can include personal information
- Can describe role in group
- Can include relationship details
- Can have performer-specific content

## Example: Three-Performer Account

Based on a real example of a shared BDSM content account:

### Primary Content Creator

```python
performer_1 = Performer(
    id="new",
    name="Lou",  # Personal/stage name
    disambiguation="doe-eyes-official",  # Shared account name
    aliases=[
        "Doe Eyes",
        "bratty_lou",
        "doe-eyes",
        "doe-eyes-official",
        "doeeyes",
    ],
    urls=[
        "https://onlyfans.com/doe-eyes-official",
        "https://linktr.ee/bratty_lou",
        "https://x.com/doe_eyes_bdsm",
        "https://fansly.com/doe-eyes",
        "https://www.manyvids.com/Profile/1006718685/doe-eyes/Store/Videos",
        "https://4based.com/profile/doe-eyes",
    ],
    gender="FEMALE",
    hair_color="Brown",
)
```

### Dom/Master Performer

```python
performer_2 = Performer(
    id="new",
    name="Owner",  # Role-based name
    disambiguation="doe-eyes-official",  # Same as primary
    aliases=["doe-eyes-official"],
    urls=["https://onlyfans.com/doe-eyes-official"],
    gender="MALE",
    details=(
        "• Real Amateur BDSM ❤️\n"
        "• Peeking into our kinky lifestyle as a Master slave couple 👀\n"
        "• 🔗Hardcore b/g 🖤Latex 🍑 Training\n"
        "• Written stories of our adventures combined with pictures and videos 🔥\n"
        "• An active community 💬\n"
        "• Regular streams which we record and post"
    ),
)
```

### Additional Performer

```python
performer_3 = Performer(
    id="new",
    name="Sugar",  # Personal/stage name
    disambiguation="doe-eyes-official",  # Same as others
    aliases=["doe-eyes-official"],
    urls=["https://onlyfans.com/doe-eyes-official"],
    gender="FEMALE",
    hair_color="Blond",
)
```

## Scene/Gallery Assignment

When processing content from shared accounts:

1. Check content description/tags for performer hints
2. Look for performer-specific tags or mentions
3. Default to including all account performers if unclear
4. Allow manual correction of performer assignments
5. Consider using tags to mark performer roles

Example:

```python
async def process_post_performers(
    post: Post,
    account_performers: list[Performer],
) -> list[Performer]:
    """Determine which performers are in a post."""
    performers = []

    # Always include mentioned performers
    for mention in post.accountMentions:
        performer = find_performer_by_account(mention)
        if performer:
            performers.append(performer)

    # Check content for performer hints
    content = post.content.lower() if post.content else ""
    for performer in account_performers:
        # Check performer names/aliases in content
        if any(alias.lower() in content for alias in performer.aliases):
            performers.append(performer)

    # If no specific performers found, include all account performers
    if not performers:
        performers = account_performers

    return performers
```
