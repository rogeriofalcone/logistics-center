# coding: utf-8
# © 2015 David BEAL @ Akretion
# © 2015 Sebastien BEAU @ Akretion
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from openerp.osv import orm
from openerp.tools.translate import _
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.session import ConnectorSession
from .common import LogisticDialect as dialect, SKU_SUFFIX
import logging
import base64


_logger = logging.getLogger(__name__)


# file_doc.file_type and repo_task.type must be the same for import
# ensure to define manually 'type' of import tasks
# call it FLOWS[file_doc.file_type]
FLOW_PARAMS = {
    'logistic_incoming': {
        'model_table': 'stock.picking.in',
        'fields': ['type', 'picking', 'move', 'product_id', 'ean', 'qty_due',
                   'real_qty', '_'],
    },
    'logistic_delivery': {
        'model_table': 'stock.picking.out',
        'fields': ['_', 'picking', '_', 'product_id', 'ean', 'qty',
                   '_', 'carrier', '_', 'tracking', 'status'],
    },
}

FILE_FLOWS = {
    'PAH': 'logistic_incoming',
    'ODC': 'logistic_delivery',
}


def is_numeric(data):
    try:
        data = str(data)
    except Exception:
        return False
    return all(char in "0123456789.+-" for char in data)


def filter_field_names(fields):
    if '_' in fields:
        fields = list(set(fields))
        del fields[fields.index('_')]
    return fields


class RepositoryTask(orm.Model):
    _inherit = 'repository.task'

    def move_file(self, cr, uid, connection, task, file_name,
                  folder_path, context=None):
        new_filename = '_' + file_name.replace('BLECKMANN', '')
        connection.move(folder_path, folder_path, file_name, new_filename)


class FileDocument(orm.Model):
    _inherit = "file.document"

    def filter_lines(self, cr, uid, file_doc, raw_lines, startswith=None,
                     context=None):
        """ Here is a basic filtering rule
            You can define your own implementation """
        nb_lines = 0
        lines = []
        for line in raw_lines:
            if startswith:
                if line.startswith(startswith):
                    lines.append(line)
                    nb_lines += 1
        if not nb_lines:
            _logger.info(
                "The file document id '%s' is empty, "
                "no need to create import" % file_doc.id)
        return lines

    def create(self, cr, uid, vals, context=None):
        " Guess flow from imported file content to set the right file_type"
        if 'datas' in vals:
            tmp = base64.b64decode(vals['datas'])
            file_flow = tmp[:3]
            del tmp
            if file_flow in FILE_FLOWS:
                vals.update({'file_type': FILE_FLOWS[file_flow]})
        return super(FileDocument, self).create(cr, uid, vals, context=context)

    def get_product_id(self, cr, uid, product, file_doc, line, context=None):
        """ Bleckmann require an extension to product (e.g. .ZZ, .OS)
            You may strip this extension with your own method
        """
        product_m = self.pool['product.product']
        suffix_pos = product.index(SKU_SUFFIX)
        if not suffix_pos:
            raise orm.except_orm(
                _("Error in Bleckmann file"),
                _("Sku column should have this suffix '%s'.\n"
                  "'%s' found" % (SKU_SUFFIX, product)))
        product_code = product[:suffix_pos]
        product_ids = product_m.search(
            cr, uid, [
                ('default_code', 'ilike', product_code),
                ('active', 'in', [True, False])], context=context)
        if not product_ids:
            raise orm.except_orm(
                "Product code error",
                "Le product '%s' isn't found in odoo" % product_code)
        return product_m.browse(cr, uid, product_ids[0], context=context).id

    def check_bleckmann_data(
            self, cr, uid, file_doc, data, dtype, line, context=None):
        message = (_(" '%s' return by bleckmann file "
                     "(file.document id '%s' at line %s) "
                     "should be numeric type")
                   % (data, file_doc.id, line))
        if dtype == 'product' and not is_numeric(data):
            raise orm.except_orm(
                _("Product id error"), _("Product id") + message)
        elif dtype == 'quantity' and not is_numeric(data):
            raise orm.except_orm(_("Quantity error"), _("Quantity") + message)
        elif dtype == 'd_order' and not is_numeric(data):
            raise orm.except_orm(_("Order id error"),
                                 _("Delivery order id") + message)
        elif dtype == 'i_order' and not is_numeric(data):
            raise orm.except_orm(_("Order id error"),
                                 _("Incoming order id") + message)
        return True

    def import_delivery(self, cr, uid, file_doc, flow, session, context=None):
        struct = {}
        line_number = 0
        for line in self.get_datas_from_file(
                cr, uid, file_doc, flow['fields'], dialect, context=context):
            line_number += 1
            self.check_bleckmann_data(cr, uid, file_doc, line['picking'],
                                      'd_order', line_number, context=context)
            picking_id = int(line['picking'])
            self.check_bleckmann_data(cr, uid, file_doc, line['qty'],
                                      'quantity', line_number, context=context)
            move = {'product_id': self.get_product_id(
                    cr, uid, line['product_id'], file_doc, line_number,
                    context=context),
                    'product_qty': float(line['qty']),
                    # 'ean': int(line['ean']),
                    'carrier': line['carrier']}
            picking = {}
            if line['tracking'] != '':
                picking = {'carrier_tracking_ref': line['tracking']}
            if picking_id in struct:
                struct[picking_id]['moves'].append(move)
            else:
                struct.update({picking_id: {'moves': [move]}})
            struct[picking_id].update({'picking': picking})
        if struct:
            _logger.info(" >>> 'import_delivery' IMPORT data")
        else:
            _logger.info(" >>> 'import_delivery' no data")
        return struct

    def import_incoming(self, cr, uid, file_doc, flow, session, context=None):
        struct = {}
        line_number = 0
        for line in self.get_datas_from_file(
                cr, uid, file_doc, flow['fields'], dialect, context=context):
            line_number += 1
            if line['type'] == 'PAL':
                self.check_bleckmann_data(
                    cr, uid, file_doc, line['picking'],
                    'i_order', line_number, context=context)
                picking_id = int(line['picking'])
                self.check_bleckmann_data(
                    cr, uid, file_doc, line['qty_due'],
                    'quantity', line_number, context=context)
                self.check_bleckmann_data(
                    cr, uid, file_doc, line['real_qty'],
                    'quantity', line_number, context=context)
                if line['qty_due'] != line['real_qty']:
                    _logger.info(
                        "\nReal qty '%s' is different from due qty '%s'"
                        % (line['real_qty'], line['qty_due']))
                move = {'product_id': self.get_product_id(
                        cr, uid, line['product_id'], file_doc, line_number,
                        context=context),
                        'product_qty': float(line['real_qty']),
                        'ean': int(line['ean'] or 0),
                        }
                picking = {}
                if picking_id in struct:
                    struct[picking_id]['moves'].append(move)
                else:
                    struct.update({picking_id: {'moves': [move]}})
                struct[picking_id].update({'picking': picking})
        if struct:
            _logger.info(" >>> 'import_incoming' IMPORT data")
        else:
            _logger.info(" >>> 'import_incoming' no data")
        return struct

    def import_datas(self, cr, uid, file_doc, backend, context=None):
        if file_doc.file_type not in FLOW_PARAMS.keys():
            flow_type = FLOW_PARAMS.keys()
            LOGISTIC_TYPE_ERROR = _("""
The File Document type (file_type) '%s' doesn't match these types %s
Probably, you don't have correctly fill tasks in the 'logistics backend'
with the right type.
Fill the right type in Task and in File Document and click on run again"""
                                    % (file_doc.file_type, flow_type))
            raise orm.except_orm(_("Error in file document type"),
                                 LOGISTIC_TYPE_ERROR)
        flow = FLOW_PARAMS[file_doc.file_type]
        session = ConnectorSession(cr, uid, context)
        struct = False
        method = 'import_' + file_doc.file_type[9:]
        struct = getattr(self, method)(
            cr, uid, file_doc, flow, session, context=context)
        if struct:
            priority = 100
            fields = filter_field_names(flow['fields'])
            for picking_id in struct:
                buffer_id = session.create('connector.buffer', {
                    'data': {picking_id: struct[picking_id]},
                })
                session.context['buffer_id'] = buffer_id
                import_one_line_from_file.delay(
                    session, flow['model_table'], fields, buffer_id,
                    file_doc.id, priority=priority)
                priority += 1
                file_doc._set_state('running', context=context)
        return True


@job
def import_one_line_from_file(
        sess, model_name, fields, buffer_id, file_doc_id, priority=None):
    datas = sess.browse('connector.buffer', buffer_id).data
    for picking_id in datas:
        sess.pool[model_name].run_job(
            sess.cr, sess.uid, picking_id, buffer_id, file_doc_id,
            datas[picking_id]['moves'],
            picking_vals=datas[picking_id]['picking'], context=sess.context)
