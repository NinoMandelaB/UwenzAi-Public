# capp/tests_rolling_deploy.py
"""
Rolling-deploy safety for recently added columns.

Railway runs the Backend's pre-deploy migrate first and replaces the other
services afterwards, so for a while processes from the previous release write
to the migrated schema. Their INSERTs leave out every column they do not know.
A NOT NULL column therefore needs a database DEFAULT, not just a Django-side
default, or those inserts fail with an IntegrityError.

0108 added ``character_gallery_images.publish_on_approval`` without one: chat
images (imageworker) and gallery generation on the old Backend container
failed until those services ran the new code. 0109 restores the DEFAULT.
"""

from django.db.models import AutoField
from django.test import TestCase

from capp.models import Character, CharacterGalleryImage
from capp.tests import _make_character, _make_user


def _insert_like_previous_release(instance, omitted_field):
    """
    Save ``instance`` the way the release before ``omitted_field`` existed did.

    ``QuerySet._insert`` is what ``Model.save()`` runs; given every concrete
    field except the new one it sends exactly the INSERT that code sends, so
    the database alone decides the omitted column. Returns the new primary key.
    """
    model = type(instance)
    fields = [
        field
        for field in model._meta.local_concrete_fields
        if field.name != omitted_field and not (field.primary_key and isinstance(field, AutoField))
    ]
    rows = model._base_manager._insert([instance], fields=fields, returning_fields=[model._meta.pk])
    return rows[0][0]


class GalleryImageRollingDeployTests(TestCase):
    def setUp(self):
        self.owner = _make_user(username="rollowner", email="rollowner@example.com")
        self.character = _make_character(name="Rolling", slug="rolling", created_by=self.owner)

    def test_insert_without_publish_on_approval_succeeds_and_defaults_to_false(self):
        image = CharacterGalleryImage(
            character=self.character,
            image="characters/gallery/rolling.webp",
            created_by=self.owner,
            source=Character.AvatarSource.AI_GENERATED,
            visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE,
        )

        pk = _insert_like_previous_release(image, "publish_on_approval")

        stored = CharacterGalleryImage.objects.get(pk=pk)
        self.assertIs(stored.publish_on_approval, False)
        self.assertEqual(stored.visibility, CharacterGalleryImage.VisibilityChoices.PRIVATE)

    def test_current_code_still_writes_the_flag_explicitly(self):
        image = CharacterGalleryImage.objects.create(
            character=self.character,
            image="characters/gallery/current.webp",
            created_by=self.owner,
            publish_on_approval=True,
        )
        image.refresh_from_db()
        self.assertTrue(image.publish_on_approval)
