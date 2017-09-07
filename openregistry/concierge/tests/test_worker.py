# -*- coding: utf-8 -*-
import os
from copy import deepcopy
from json import load

import pytest
from munch import munchify

from openregistry.concierge.worker import logger as LOGGER
from openprocurement_client.exceptions import (
    Forbidden,
    ResourceNotFound,
    RequestFailed,
    UnprocessableEntity
)

ROOT = os.path.dirname(__file__) + '/data/'


def test_get_lot(bot, logger, mocker):
    mock_continuous_changes_feed = mocker.patch('openregistry.concierge.worker.continuous_changes_feed', autospec=True)
    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)
    mock_continuous_changes_feed.return_value = (lot['data'] for lot in lots)

    result = bot.get_lot()

    assert 'next' and '__iter__' in dir(result)  # assert generator object is returned

    assert result.next() == lots[0]['data']
    assert result.next() == lots[1]['data']

    with pytest.raises(StopIteration):
        result.next()

    log_strings = logger.log_capture_string.getvalue().split('\n')

    assert log_strings[0] == 'Getting Lots'


def test_run(bot, logger, mocker, almost_always_true):
    mock_get_lot = mocker.patch.object(bot, 'get_lot', autospec=True)
    mock_process_lots = mocker.patch.object(bot, 'process_lots', autospec=True)
    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)
    for lot in lots:
        lot['data']['rev'] = '123'
    mock_get_lot.return_value = (lot['data'] for lot in lots)

    mocker.patch('openregistry.concierge.worker.True', almost_always_true(2))

    if bot.errors_doc.get(lots[0]['data']['id'], None):
        del bot.errors_doc[lots[0]['data']['id']]
    if bot.errors_doc.get(lots[1]['data']['id'], None):
        del bot.errors_doc[lots[1]['data']['id']]
    bot.db.save(bot.errors_doc)

    bot.run()

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == "Starting worker"

    assert mock_get_lot.call_count is 2
    assert mock_process_lots.call_count == 2

    assert mock_process_lots.call_args_list[0][0][0] == lots[0]['data']
    assert mock_process_lots.call_args_list[1][0][0] == lots[1]['data']

    error_lots = deepcopy(lots)
    error_lots[1]['data']['rev'] = '234'
    for lot in error_lots:
        bot.errors_doc[lot['data']['id']] = lot['data']
    bot.db.save(bot.errors_doc)

    mocker.patch('openregistry.concierge.worker.True', almost_always_true(2))
    mock_get_lot.return_value = (lot['data'] for lot in lots)

    bot.run()
    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == "Starting worker"

    assert mock_get_lot.call_count is 4
    assert mock_process_lots.call_count == 3

    assert mock_process_lots.call_args_list[2][0][0] == error_lots[1]['data']


def test_patch_lot(bot, logger, mocker):
    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)
    mock_patch_resource_item = mocker.MagicMock()
    test_lot = deepcopy(lots[0])
    test_lot['data']['status'] = 'active.salable'
    mock_patch_resource_item.side_effect = [
        munchify(test_lot),
        Forbidden(response=munchify({"text": "Operation is forbidden."})),
        RequestFailed(response=munchify({"text": "Request failed.", "status_code": 502})),
        UnprocessableEntity(response=munchify({"text": "Unprocessable Entity."}))
    ]
    bot.lots_client.patch_resource_item = mock_patch_resource_item
    lot = lots[0]['data']
    status = 'active.salable'

    result = bot.patch_lot(lot=lot, status=status)
    assert result is True

    result = bot.patch_lot(lot=lot, status=status)
    assert result is False

    result = bot.patch_lot(lot=lot, status=status)
    assert result is False

    result = bot.patch_lot(lot=lot, status=status)
    assert result is False

    assert bot.lots_client.patch_resource_item.call_count == 4

    log_strings = logger.log_capture_string.getvalue().split('\n')

    assert log_strings[0] == 'Successfully patched lot 9ee8f769438e403ebfb17b2240aedcf1 to active.salable'
    assert log_strings[1] == 'Failed to patch lot 9ee8f769438e403ebfb17b2240aedcf1 to active.salable (Operation is forbidden.)'
    assert log_strings[2] == 'Failed to patch lot 9ee8f769438e403ebfb17b2240aedcf1 to active.salable (Server error: 502)'
    assert log_strings[3] == 'Failed to patch lot 9ee8f769438e403ebfb17b2240aedcf1 to active.salable (Unprocessable Entity.)'


def test_patch_assets_pending_success(bot, logger, mocker):
    mock_patch_resource_item = mocker.MagicMock()
    bot.assets_client.patch_resource_item = mock_patch_resource_item

    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)

    with open(ROOT + 'assets.json') as assets:
        assets = load(assets)

    lot = lots[1]['data']
    status = 'pending'

    mock_patch_resource_item.side_effect = [
        munchify(assets[4]),
        munchify(assets[5]),
        munchify(assets[6]),
        munchify(assets[7])
    ]

    result, patched_assets = bot.patch_assets(lot=lot, status=status)
    assert result is True
    assert patched_assets == [
        '0a7eba27b22a454180d3a49b02a1842f',
        '660cbb6e83c94c80baf47691732fd1b2',
        '8034c43e2d764006ad6e655e339e5fec',
        '5545b519045a4637ab880f032960e034'
    ]

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == 'Successfully patched asset 0a7eba27b22a454180d3a49b02a1842f to pending'
    assert log_strings[1] == 'Successfully patched asset 660cbb6e83c94c80baf47691732fd1b2 to pending'
    assert log_strings[2] == 'Successfully patched asset 8034c43e2d764006ad6e655e339e5fec to pending'
    assert log_strings[3] == 'Successfully patched asset 5545b519045a4637ab880f032960e034 to pending'

    assert bot.assets_client.patch_resource_item.call_count == 4


def test_patch_assets_pending_fail(bot, logger, mocker):
    mock_patch_resource_item = mocker.MagicMock()
    bot.assets_client.patch_resource_item = mock_patch_resource_item

    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)

    with open(ROOT + 'assets.json') as assets:
        assets = load(assets)

    lot = lots[1]['data']
    status = 'pending'

    mock_patch_resource_item.side_effect = [
        munchify(assets[4]),
        RequestFailed(response=munchify({"text": "Bad Gateway", "status_code": 502})),
        munchify(assets[6]),
        munchify(assets[7])
    ]

    result, patched_assets = bot.patch_assets(lot=lot, status=status)
    assert result is False
    assert patched_assets == ['0a7eba27b22a454180d3a49b02a1842f']

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == 'Successfully patched asset 0a7eba27b22a454180d3a49b02a1842f to pending'
    assert log_strings[1] == 'Failed to patch asset 660cbb6e83c94c80baf47691732fd1b2 to pending (Server error: 502)'

    assert bot.assets_client.patch_resource_item.call_count == 2

    mock_patch_resource_item.side_effect = [
        Forbidden(response=munchify({"text": "Operation is forbidden."})),
        munchify(assets[5]),
        munchify(assets[6]),
        munchify(assets[7])
    ]

    result, patched_assets = bot.patch_assets(lot=lot, status=status)
    assert result is False
    assert patched_assets == []

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[2] == 'Failed to patch asset 0a7eba27b22a454180d3a49b02a1842f to pending (Operation is forbidden.)'

    assert bot.assets_client.patch_resource_item.call_count == 3


def test_patch_assets_verification_success(bot, logger, mocker):
    mock_patch_resource_item = mocker.MagicMock()
    bot.assets_client.patch_resource_item = mock_patch_resource_item

    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)

    with open(ROOT + 'assets.json') as assets:
        assets = load(assets)

    lot = lots[0]['data']
    status = 'verification'

    mock_patch_resource_item.side_effect = [
        munchify(assets[0]),
        munchify(assets[1]),
        munchify(assets[2]),
        munchify(assets[3])
    ]

    result, patched_assets = bot.patch_assets(lot=lot, status=status, related_lot=lot['id'])
    assert result is True
    assert patched_assets == [
        'e519404fd0b94305b3b19ec60add05e7',
        '64099f8259c64215b3bd290bc12ec73a',
        'f00d0ae5032f4927a4e0c046cafd3c62',
        'c1c043ba1e3d457c8632c3b48c7279a4'
    ]

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == 'Successfully patched asset e519404fd0b94305b3b19ec60add05e7 to verification'
    assert log_strings[1] == 'Successfully patched asset 64099f8259c64215b3bd290bc12ec73a to verification'
    assert log_strings[2] == 'Successfully patched asset f00d0ae5032f4927a4e0c046cafd3c62 to verification'
    assert log_strings[3] == 'Successfully patched asset c1c043ba1e3d457c8632c3b48c7279a4 to verification'

    assert bot.assets_client.patch_resource_item.call_count == 4


def test_patch_assets_active_fail(bot, logger, mocker):
    mock_patch_resource_item = mocker.MagicMock()
    bot.assets_client.patch_resource_item = mock_patch_resource_item

    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)

    with open(ROOT + 'assets.json') as assets:
        assets = load(assets)

    lot = lots[0]['data']
    status = 'verification'

    mock_patch_resource_item.side_effect = [
        munchify(assets[0]),
        munchify(assets[1]),
        RequestFailed(response=munchify({"text": "Request failed.", "status_code": 502})),
        munchify(assets[3])
    ]

    result, patched_assets = bot.patch_assets(lot=lot, status=status, related_lot=lot['id'])
    assert result is False
    assert patched_assets == ['e519404fd0b94305b3b19ec60add05e7', '64099f8259c64215b3bd290bc12ec73a']

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == 'Successfully patched asset e519404fd0b94305b3b19ec60add05e7 to verification'
    assert log_strings[1] == 'Successfully patched asset 64099f8259c64215b3bd290bc12ec73a to verification'
    assert log_strings[2] == 'Failed to patch asset f00d0ae5032f4927a4e0c046cafd3c62 to verification (Server error: 502)'

    assert bot.assets_client.patch_resource_item.call_count == 3


def test_process_lots(bot, logger, mocker):
    mock_check_lot = mocker.patch.object(bot, 'check_lot', autospec=True)
    mock_check_lot.side_effect = [True, True, True, True, True, False]

    mock_check_assets = mocker.patch.object(bot, 'check_assets', autospec=True)
    mock_check_assets.side_effect = [
        True, True,
        False,
        RequestFailed(response=munchify({"text": "Request failed."}))
    ]

    mock_patch_assets = mocker.patch.object(bot, 'patch_assets', autospec=True)
    mock_patch_assets.side_effect = [
        (False, []),
        (True, []), (True, ['all_assets']), (True, ['all_assets']), (True, ['all_assets'])
    ]

    mock_patch_lot = mocker.patch.object(bot, 'patch_lot', autospec=True)
    mock_patch_lot.return_value = True

    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)

    verification_lot = lots[0]['data']
    dissolved_lot = lots[1]['data']

    # status == 'verification'
    bot.process_lots(verification_lot)  # assets_available: True; patch_assets: [(False, []), (True, []]; check_lot: True

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == 'Processing lot 9ee8f769438e403ebfb17b2240aedcf1'

    assert mock_check_assets.call_count == 1
    assert mock_check_assets.call_args[0] == (verification_lot,)

    assert mock_check_lot.call_count == 1
    assert mock_check_lot.call_args[0] == (verification_lot,)

    assert mock_patch_assets.call_count == 1
    assert mock_patch_assets.call_args_list[0][0] == (verification_lot, 'verification', verification_lot['id'])

    bot.process_lots(verification_lot)  # assets_available: True; patch_assets: [(True, []), (True, [])]; check_lot: True

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[1] == 'Processing lot 9ee8f769438e403ebfb17b2240aedcf1'

    assert mock_check_assets.call_count == 2
    assert mock_check_assets.call_args[0] == (verification_lot,)

    assert mock_check_lot.call_count == 2
    assert mock_check_lot.call_args[0] == (verification_lot,)

    assert mock_patch_assets.call_count == 3
    assert mock_patch_assets.call_args_list[1][0] == (verification_lot, 'verification', verification_lot['id'])
    assert mock_patch_assets.call_args_list[2][0] == (verification_lot, 'active', verification_lot['id'])

    assert mock_patch_lot.call_count == 1
    assert mock_patch_lot.call_args[0] == (verification_lot, 'active.salable')

    bot.process_lots(verification_lot)  # assets_available: False; patch_assets: None; check_lot: True

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[2] == 'Processing lot 9ee8f769438e403ebfb17b2240aedcf1'

    assert mock_check_assets.call_count == 3
    assert mock_check_assets.call_args[0] == (verification_lot,)

    assert mock_check_lot.call_count == 3
    assert mock_check_lot.call_args[0] == (verification_lot,)

    assert mock_patch_lot.call_count == 2
    assert mock_patch_lot.call_args[0] == (verification_lot, 'pending')

    bot.process_lots(verification_lot)  # assets_available: raises exception; patch_assets: None; check_lot: True

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[3] == 'Processing lot 9ee8f769438e403ebfb17b2240aedcf1'
    assert log_strings[4] == 'Due to fail in getting assets, lot 9ee8f769438e403ebfb17b2240aedcf1 is skipped'

    assert mock_check_assets.call_count == 4
    assert mock_check_assets.call_args[0] == (verification_lot,)

    assert mock_check_lot.call_count == 4
    assert mock_check_lot.call_args[0] == (verification_lot,)

    # status == 'dissolved'
    bot.process_lots(dissolved_lot)  # assets_available: None; patch_assets: (True, []); check_lot: True

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[5] == 'Processing lot b844573afaa24e4fb098f3027e605c87'

    assert mock_check_lot.call_count == 5
    assert mock_check_lot.call_args[0] == (dissolved_lot,)

    assert mock_patch_assets.call_count == 4
    assert mock_patch_assets.call_args[0] == (dissolved_lot, 'pending')

    # lot is not available
    bot.process_lots(dissolved_lot)  # assets_available: None; patch_assets: None; check_lot: False

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[6] == 'Skipping lot b844573afaa24e4fb098f3027e605c87'

    assert mock_check_lot.call_count == 6
    assert mock_check_lot.call_args[0] == (dissolved_lot,)


def test_process_lots_broken(bot, logger, mocker):

    mock_log_broken_lot = mocker.patch('openregistry.concierge.worker.log_broken_lot', autospec=True)

    mock_check_lot = mocker.patch.object(bot, 'check_lot', autospec=True)
    mock_check_lot.return_value = True

    mock_check_assets = mocker.patch.object(bot, 'check_assets', autospec=True)
    mock_check_assets.return_value = True

    mock_patch_assets = mocker.patch.object(bot, 'patch_assets', autospec=True)
    mock_patch_assets.side_effect = [
        (False, ['successfully_patched_assets']), (False, []),
        (True, ['']), (False, ['successfully_patched_assets']), (False, []),
        (True, []), (True, [])
    ]

    mock_patch_lot = mocker.patch.object(bot, 'patch_lot', autospec=True)
    mock_patch_lot.return_value = False

    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)

    lot = lots[0]['data']

    # failed on patching assets to verification
    bot.process_lots(lot)  # patch_assets: [False, False]

    assert mock_patch_assets.call_count == 2
    assert mock_patch_assets.call_args_list[0][0] == (lot, 'verification', lot['id'])
    assert mock_patch_assets.call_args_list[1][0] == ({'assets': ['successfully_patched_assets']}, 'pending')

    assert mock_log_broken_lot.call_count == 1
    assert mock_log_broken_lot.call_args_list[0][0] == (
        bot.db, LOGGER, bot.errors_doc, lot, 'patching assets to verification'
    )

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == 'Processing lot 9ee8f769438e403ebfb17b2240aedcf1'
    assert log_strings[1] == "Assets ['successfully_patched_assets'] will be repatched to 'pending'"

    # failed on patching assets to active
    bot.process_lots(lot)  # patch_assets: [True, False, False]

    assert mock_patch_assets.call_count == 5
    assert mock_patch_assets.call_args_list[2][0] == (lot, 'verification', lot['id'])
    assert mock_patch_assets.call_args_list[3][0] == (lot, 'active', lot['id'])
    assert mock_patch_assets.call_args_list[4][0] == (lot, 'pending')

    assert mock_log_broken_lot.call_count == 2
    assert mock_log_broken_lot.call_args_list[1][0] == (
        bot.db, LOGGER, bot.errors_doc, lot, 'patching assets to active'
    )

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[2] == 'Processing lot 9ee8f769438e403ebfb17b2240aedcf1'
    assert log_strings[3] == "Assets [u'e519404fd0b94305b3b19ec60add05e7', u'64099f8259c64215b3bd290bc12ec73a'," \
                             " u'f00d0ae5032f4927a4e0c046cafd3c62', u'c1c043ba1e3d457c8632c3b48c7279a4'] will" \
                             " be repatched to 'pending'"

    # failed on patching lot to active.salable
    bot.process_lots(lot)  # patch_assets: [True, True]; patch_lot: False

    assert mock_patch_assets.call_count == 7
    assert mock_patch_assets.call_args_list[5][0] == (lot, 'verification', lot['id'])
    assert mock_patch_assets.call_args_list[6][0] == (lot, 'active', lot['id'])

    assert mock_log_broken_lot.call_count == 3
    assert mock_log_broken_lot.call_args_list[2][0] == (
        bot.db, LOGGER, bot.errors_doc, lot, 'patching lot to active.salable'
    )

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[4] == 'Processing lot 9ee8f769438e403ebfb17b2240aedcf1'


def test_check_assets(bot, logger, mocker):
    with open(ROOT + 'assets.json') as assets:
        assets = load(assets)

    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)

    verification_lot = deepcopy(lots[0]['data'])
    verification_lot['assets'] = ['e519404fd0b94305b3b19ec60add05e7']
    dissolved_lot = deepcopy(lots[1]['data'])
    dissolved_lot['assets'] = ["0a7eba27b22a454180d3a49b02a1842f"]

    mock_get_asset = mocker.MagicMock()
    mock_get_asset.side_effect = [
        RequestFailed(response=munchify({"text": "Request failed.", "status_code": 502})),
        ResourceNotFound(response=munchify({"text": "Asset could not be found."})),
        munchify(assets[0]),
        munchify(assets[7])
    ]

    bot.assets_client.get_asset = mock_get_asset

    with pytest.raises(RequestFailed):
        bot.check_assets(verification_lot)

    result = bot.check_assets(verification_lot)
    assert result is False

    result = bot.check_assets(verification_lot)
    assert result is True

    result = bot.check_assets(dissolved_lot)
    assert result is False

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == "Falied to get asset e519404fd0b94305b3b19ec60add05e7. Status code: 502"
    assert log_strings[1] == "Falied to get asset e519404fd0b94305b3b19ec60add05e7: Asset could not be found."
    assert log_strings[2] == "Successfully got asset e519404fd0b94305b3b19ec60add05e7"
    assert log_strings[3] == "Successfully got asset 0a7eba27b22a454180d3a49b02a1842f"


def test_check_lot(bot, logger, mocker):

    with open(ROOT + 'lots.json') as lots:
        lots = load(lots)

    lot = deepcopy(lots[0]['data'])
    wrong_status_lot = deepcopy(lot)
    wrong_status_lot['status'] = 'pending'

    mock_get_lot = mocker.MagicMock()
    mock_get_lot.side_effect = [
        RequestFailed(response=munchify({"text": "Request failed.", "status_code": 502})),
        ResourceNotFound(response=munchify({"text": "Lot could not be found."})),
        munchify({"data": lot}),
        munchify({"data": wrong_status_lot})
    ]

    bot.lots_client.get_lot = mock_get_lot

    result = bot.check_lot(lot)
    assert result is False

    result = bot.check_lot(lot)
    assert result is False

    result = bot.check_lot(lot)
    assert result is True

    result = bot.check_lot(wrong_status_lot)
    assert result is False

    log_strings = logger.log_capture_string.getvalue().split('\n')
    assert log_strings[0] == "Falied to get lot 9ee8f769438e403ebfb17b2240aedcf1. Status code: 502"
    assert log_strings[1] == "Falied to get lot 9ee8f769438e403ebfb17b2240aedcf1: Lot could not be found."
    assert log_strings[2] == "Successfully got lot 9ee8f769438e403ebfb17b2240aedcf1"
    assert log_strings[3] == "Successfully got lot 9ee8f769438e403ebfb17b2240aedcf1"
    assert log_strings[4] == "Lot 9ee8f769438e403ebfb17b2240aedcf1 can not be processed in current status ('pending')"
